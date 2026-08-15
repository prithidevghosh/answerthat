"""The append-only `source_store`. HR-1 lives here.

This is the mechanism that makes fabrication structurally impossible rather than
discouraged. `source_id` is a foreign key; an LLM anywhere in this system cannot mint a
source, it can only *reference* one that a real HTTP response already put here. Everything
else in the project — the kernel's REJECT rule 1, every clickable finding, the honesty
audit — rests on that being true, so it is enforced by four independent runtime checks
rather than by a comment:

1. **Caller check.** `put()` inspects its caller's module and refuses anything outside
   `app.providers`. If `app/agent/` or `app/review/` ever calls it, it raises.
2. **Provenance check.** The record's `Provenance` must have been minted by
   `ProviderResponse.provenance()` from a real response and registered in
   `PROVENANCE_REGISTRY`. A hand-built or model-built provenance is refused *even from
   inside `app/providers/`*.
3. **URL check.** `external_url` must be absolute http(s) with a host. A finding a reader
   cannot open is indistinguishable from a fabricated one.
4. **Append-only check.** An existing `source_id` may only be *enriched* — an abstract
   arriving later through the fallback chain — as a new version. No field may change
   value or revert to null, and no row is ever updated or deleted.

If you are reading this because a check is in your way: the fix is to route the write
through a provider adapter backed by a real response. Never relax a check.

## Sync reads

Appendix A and `app/agent/ports.py` both declare `get`/`has` as *sync*, while the store
is backed by async Postgres. Both are served from a complete in-process index, which the
caller populates with `await warm(ids)` first. A sync `has()` for an id that was never
warmed **raises** rather than returning `False`: "we never looked" reported as "it does
not exist" is precisely the HR-3 failure, and here it would produce a false REJECT.
See the Interface Request in `memory.md` §5.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.contracts import AbstractSource, Provenance, SourceRecord
from app.core.db import JSONB, Base, session_scope, utcnow
from app.core.errors import SourceStoreViolation
from app.providers.errors import AppendOnlyViolation, UnprovenanceredSource
from app.providers.http import PROVENANCE_REGISTRY, is_resolvable_url

__all__ = [
    "SourceStoreRow",
    "PostgresSourceStore",
    "InMemorySourceStore",
    "SourceNotIndexed",
    "PROVIDER_PACKAGE",
]

#: Only modules under this package may write. HR-1.
PROVIDER_PACKAGE = "app.providers"

# Absent values, for the append-only enrichment rule. An abstract going from "we have not
# found one" to a real string is enrichment; anything else is a mutation.
_ABSENT_ABSTRACT_SOURCES = {AbstractSource.UNAVAILABLE}


class SourceNotIndexed(RuntimeError):
    """A sync `get`/`has` asked about a `source_id` that was never loaded.

    Not an absence — an unknown. Raised so the caller warms the index instead of
    concluding the source does not exist (HR-3).
    """

    def __init__(self, source_id: str) -> None:
        super().__init__(
            f"source_id {source_id!r} has not been loaded into the store index. "
            "Call `await store.warm([...])` with every source_id you intend to check "
            "before using the sync accessors. Refusing to answer 'absent' for an id we "
            "never looked up."
        )


def _calling_module(depth: int) -> str:
    """Module name `depth` frames above this function."""
    try:
        return sys._getframe(depth).f_globals.get("__name__", "<unknown>")
    except ValueError:  # pragma: no cover - stack shallower than expected
        return "<unknown>"


def _assert_provider_caller(depth: int = 3) -> None:
    """Refuse writes originating outside `app/providers/`. HR-1, check 1."""
    caller = _calling_module(depth)
    if caller == PROVIDER_PACKAGE or caller.startswith(PROVIDER_PACKAGE + "."):
        return
    raise SourceStoreViolation(
        f"{caller} attempted to write to source_store. Only {PROVIDER_PACKAGE}.* may "
        "write, and only from a real HTTP response (HR-1). If this module needs a new "
        "source, it must call a provider adapter — the adapter writes, and returns the "
        "source_id."
    )


def _assert_real_provenance(record: SourceRecord) -> None:
    """Refuse provenance no HTTP response minted, and unopenable URLs. HR-1, checks 2 & 3."""
    if not PROVENANCE_REGISTRY.contains(record.provenance):
        raise UnprovenanceredSource(
            f"source_id {record.source_id!r} carries a Provenance that was not minted by "
            "ProviderHTTP from a real response. Build it with "
            "`response.provenance(external_url=...)`; a hand-constructed Provenance is "
            "exactly what HR-1 exists to reject."
        )
    if not is_resolvable_url(record.provenance.external_url):
        raise SourceStoreViolation(
            f"source_id {record.source_id!r} has external_url "
            f"{record.provenance.external_url!r}, which is not an absolute http(s) URL. "
            "Provenance must be checkable by a reader."
        )


def _merge_append_only(existing: SourceRecord, incoming: SourceRecord) -> SourceRecord | None:
    """Return the enriched record to append, or None if there is nothing new.

    Raises `AppendOnlyViolation` if `incoming` would change any value already stored.
    """
    conflicts: list[str] = []

    merged_csl = dict(existing.csl)
    for key, value in (incoming.csl or {}).items():
        if key not in merged_csl or merged_csl[key] in (None, "", [], {}):
            merged_csl[key] = value
        elif merged_csl[key] != value:
            conflicts.append(f"csl.{key}: {merged_csl[key]!r} -> {value!r}")

    abstract = existing.abstract
    abstract_source = existing.abstract_source
    existing_has_abstract = bool(existing.abstract) and (
        existing.abstract_source not in _ABSENT_ABSTRACT_SOURCES
    )
    incoming_has_abstract = bool(incoming.abstract) and (
        incoming.abstract_source not in _ABSENT_ABSTRACT_SOURCES
    )
    if incoming_has_abstract and not existing_has_abstract:
        abstract = incoming.abstract
        abstract_source = incoming.abstract_source
    elif incoming_has_abstract and existing.abstract != incoming.abstract:
        # Two providers disagreeing on an abstract is real and interesting, but the first
        # one we stored is the one whose quote a finding may already be checked against.
        # Refuse rather than pick, so the disagreement surfaces.
        conflicts.append(
            f"abstract from {existing.abstract_source.value} would be replaced by "
            f"{incoming.abstract_source.value}"
        )

    if conflicts:
        raise AppendOnlyViolation(
            f"source_id {existing.source_id!r} is already stored and this write would "
            "change stored values, which the append-only store does not permit: "
            + "; ".join(conflicts)
        )

    if merged_csl == existing.csl and abstract == existing.abstract:
        return None

    return SourceRecord(
        source_id=existing.source_id,
        csl=merged_csl,
        # The provenance of the *enriching* call — it is the response that justifies the
        # new field. The original stays on the earlier version, which is never deleted.
        provenance=incoming.provenance,
        abstract=abstract,
        abstract_source=abstract_source,
    )


# --------------------------------------------------------------------------- table


class SourceStoreRow(Base):
    """One version of one source. APPEND-ONLY: rows are inserted, never updated or deleted.

    `(source_id, version)` is the primary key. `version` increments when a later response
    enriches a record — an abstract arriving through the fallback chain, say — so the
    audit trail of exactly what each provider told us, and when, is preserved intact.
    """

    __tablename__ = "source_store"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    csl: Mapped[dict] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    abstract_source: Mapped[str] = mapped_column(String(32), nullable=False)
    written_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_record(self) -> SourceRecord:
        return SourceRecord(
            source_id=self.source_id,
            csl=self.csl,
            provenance=Provenance(**self.provenance),
            abstract=self.abstract,
            abstract_source=AbstractSource(self.abstract_source),
        )


# --------------------------------------------------------------------------- stores


class _BaseSourceStore:
    """Guards and index bookkeeping shared by both stores.

    The guards live here rather than in each subclass so there is exactly one place a
    check could be weakened, and it is a place with this docstring attached to it.
    """

    def __init__(self) -> None:
        self._index: dict[str, SourceRecord] = {}
        self._known_absent: set[str] = set()
        self.writes = 0
        self.enrichments = 0
        self.no_ops = 0

    # ---- sync reads, served from the warmed index (Appendix A / agent ports) ----

    def get(self, source_id: str) -> SourceRecord | None:
        if source_id in self._index:
            return self._index[source_id]
        if source_id in self._known_absent:
            return None
        raise SourceNotIndexed(source_id)

    def has(self, source_id: str) -> bool:
        if source_id in self._index:
            return True
        if source_id in self._known_absent:
            return False
        raise SourceNotIndexed(source_id)

    def _index_record(self, record: SourceRecord) -> None:
        self._index[record.source_id] = record
        self._known_absent.discard(record.source_id)

    def _index_absent(self, source_id: str) -> None:
        if source_id not in self._index:
            self._known_absent.add(source_id)

    def indexed_ids(self) -> set[str]:
        return set(self._index)


class PostgresSourceStore(_BaseSourceStore):
    """The production store."""

    async def put(self, record: SourceRecord) -> str:
        # depth 3: _assert_provider_caller -> put -> caller.
        _assert_provider_caller(3)
        _assert_real_provenance(record)

        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(SourceStoreRow)
                    .where(SourceStoreRow.source_id == record.source_id)
                    .order_by(SourceStoreRow.version.desc())
                    .limit(1)
                )
            ).scalars().all()

            if not rows:
                session.add(_to_row(record, version=1))
                self.writes += 1
                self._index_record(record)
                return record.source_id

            existing = rows[0].to_record()
            enriched = _merge_append_only(existing, record)
            if enriched is None:
                self.no_ops += 1
                self._index_record(existing)
                return existing.source_id

            session.add(_to_row(enriched, version=rows[0].version + 1))
            self.enrichments += 1
            self._index_record(enriched)
            return enriched.source_id

    async def warm(self, source_ids: Iterable[str]) -> None:
        """Load these ids into the index so the sync accessors can answer.

        Ids with no row are recorded as *known absent*, which is what lets `has()` return
        `False` for a fabricated id instead of raising.
        """
        wanted = {sid for sid in source_ids if sid}
        missing = wanted - set(self._index)
        if not missing:
            return
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(SourceStoreRow)
                    .where(SourceStoreRow.source_id.in_(missing))
                    .order_by(SourceStoreRow.source_id, SourceStoreRow.version)
                )
            ).scalars().all()
        latest: dict[str, SourceStoreRow] = {}
        for row in rows:
            latest[row.source_id] = row  # ordered ascending, so the last one wins
        for source_id in missing:
            row = latest.get(source_id)
            if row is None:
                self._index_absent(source_id)
            else:
                self._index_record(row.to_record())

    async def fetch(self, source_id: str) -> SourceRecord | None:
        """Async read straight from the database, warming the index as a side effect."""
        await self.warm([source_id])
        return self.get(source_id)

    async def history(self, source_id: str) -> list[SourceRecord]:
        """Every version, oldest first. The audit trail HR-1 promises."""
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(SourceStoreRow)
                    .where(SourceStoreRow.source_id == source_id)
                    .order_by(SourceStoreRow.version)
                )
            ).scalars().all()
        return [row.to_record() for row in rows]


class InMemorySourceStore(_BaseSourceStore):
    """Process-local store for unit tests and recorded-fixture runs.

    Carries the identical guards — a test that passes against this one is testing HR-1,
    not a relaxed copy of it. Not a production fallback; nothing constructs it implicitly.
    """

    def __init__(self) -> None:
        super().__init__()
        self._versions: dict[str, list[SourceRecord]] = {}

    async def put(self, record: SourceRecord) -> str:
        _assert_provider_caller(3)
        _assert_real_provenance(record)

        versions = self._versions.get(record.source_id)
        if not versions:
            self._versions[record.source_id] = [record]
            self.writes += 1
            self._index_record(record)
            return record.source_id

        enriched = _merge_append_only(versions[-1], record)
        if enriched is None:
            self.no_ops += 1
            self._index_record(versions[-1])
            return record.source_id
        versions.append(enriched)
        self.enrichments += 1
        self._index_record(enriched)
        return enriched.source_id

    async def warm(self, source_ids: Iterable[str]) -> None:
        for source_id in source_ids:
            versions = self._versions.get(source_id)
            if versions:
                self._index_record(versions[-1])
            else:
                self._index_absent(source_id)

    async def fetch(self, source_id: str) -> SourceRecord | None:
        await self.warm([source_id])
        return self.get(source_id)

    async def history(self, source_id: str) -> list[SourceRecord]:
        return list(self._versions.get(source_id, []))


def _to_row(record: SourceRecord, *, version: int) -> SourceStoreRow:
    return SourceStoreRow(
        source_id=record.source_id,
        version=version,
        csl=record.csl,
        provenance=record.provenance.model_dump(),
        abstract=record.abstract,
        abstract_source=record.abstract_source.value,
        written_at=utcnow(),
    )
