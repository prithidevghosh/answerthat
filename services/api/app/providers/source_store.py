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
4. **Append-only check.** No row is ever updated or deleted; an existing `source_id`
   only ever gains versions. A change to an identity field (`DOI`) is refused outright,
   and an abstract we already hold is never replaced — a differing one is recorded
   beside it rather than over it.

Checks 1-3 are absolute. Check 4 has a scope, set by **ADR-028**: same DOI means the same
work, so a second provider describing that work is *additional information*, not an attempt
to corrupt the first. Its extra fields are merged in; where it describes a field
differently, the stored value stays canonical and the alternative is recorded on the new
version. Choosing between two readings belongs to `Arbiter`, which has an agreement score
to do it with — this store's job is to lose nothing. Before ADR-028 every difference was
fatal, which meant cross-provider enrichment never once succeeded and a single publisher
string killed the ingest of a whole paper.

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

import logging
import sys
from collections.abc import Iterable
from typing import Any, NamedTuple

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.contracts import AbstractSource, Provenance, SourceRecord
from app.core.db import JSONB, Base, session_scope, utcnow
from app.core.errors import SourceStoreViolation
from app.providers.errors import AppendOnlyViolation, UnprovenanceredSource
from app.providers.http import PROVENANCE_REGISTRY, is_resolvable_url

__all__ = [
    "MergeResult",
    "SourceStoreRow",
    "PostgresSourceStore",
    "InMemorySourceStore",
    "SourceNotIndexed",
    "PROVIDER_PACKAGE",
]

log = logging.getLogger("app.providers.source_store")

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


#: CSL fields where a change means the identity of the work itself moved. ADR-028 keeps
#: these fatal: `source_id` is minted from the DOI, so a differing DOI under one id means
#: the minting is broken, and continuing would file one paper's evidence under another's.
_IDENTITY_CSL_FIELDS = frozenset({"DOI"})


class MergeResult(NamedTuple):
    """What a second `put()` for one `source_id` produced.

    `record` is None when there was nothing new to store. `disagreements` may be
    non-empty even then — two providers can describe a work differently without either
    adding a field, and that is still worth a version (ADR-028).
    """

    record: SourceRecord | None
    disagreements: list[dict]

    @property
    def is_noop(self) -> bool:
        return self.record is None and not self.disagreements


def _merge_dicts(existing: dict, incoming: dict) -> tuple[dict, list[tuple[str, Any, Any]]]:
    """One level of key-by-key merge. Returns the merged dict and the differing keys.

    Key-by-key rather than whole-value, which matters most for `custom`: it is the
    per-provider extension namespace, so its dicts differ *by construction* — OpenAlex
    contributes `openalex_id`, Crossref contributes `crossref_score`. Comparing it as a
    single value made two complementary bags a conflict, which is why cross-provider
    enrichment had never once succeeded (ADR-028).
    """
    merged = dict(existing)
    differing: list[tuple[str, Any, Any]] = []
    for key, value in (incoming or {}).items():
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            # `custom.providers` — each provider owns its own sub-key, so recursing keeps
            # them from colliding rather than declaring them incompatible.
            sub_merged, sub_differing = _merge_dicts(merged[key], value)
            merged[key] = sub_merged
            differing.extend((f"{key}.{k}", a, b) for k, a, b in sub_differing)
        elif merged[key] != value:
            differing.append((key, merged[key], value))
    return merged, differing


def _merge_append_only(existing: SourceRecord, incoming: SourceRecord) -> MergeResult:
    """Merge a second provider's record into the stored one. ADR-028.

    Same DOI means the same work, so the two records are two descriptions of one thing.
    Everything the incoming record adds is taken; where the two describe a field
    differently, **the stored value stays canonical and the alternative is recorded**.
    A finding's quote may already have been substring-checked against what is stored, so
    promoting a later reading would move ground the verifier already stood on — and
    choosing between readings is the arbiter's job, not the store's.

    Still fatal, and deliberately so: a change to an identity field. That is the part of
    HR-1 that makes fabrication structurally impossible.

    An abstract we already hold is still never *replaced* — that guarantee is what ADR-028
    protects and it is untouched here. What is no longer fatal is a second provider
    offering a different one, because that is the fallback chain running in its documented
    order, not an attempt to corrupt the first. ADR-006 ranks S2 → OpenAlex inverted →
    TLDR, so a record stored with an S2 TLDR and later enriched with OpenAlex's full
    inverted abstract is the *normal* path, and raising there killed the review. The rival
    reading is recorded beside the canonical one, exactly like a descriptive field.
    `AbstractResolver` remains the thing that chooses which abstract is used, and it
    re-ranks live on every resolve — the store must not decide that question a second
    time (ADR-028's whole argument against a store-side veto).
    """
    merged_csl, differing = _merge_dicts(existing.csl or {}, incoming.csl or {})

    violations = [
        f"csl.{key}: {stored!r} -> {offered!r}"
        for key, stored, offered in differing
        if key in _IDENTITY_CSL_FIELDS
    ]

    abstract = existing.abstract
    abstract_source = existing.abstract_source
    existing_has_abstract = bool(existing.abstract) and (
        existing.abstract_source not in _ABSENT_ABSTRACT_SOURCES
    )
    incoming_has_abstract = bool(incoming.abstract) and (
        incoming.abstract_source not in _ABSENT_ABSTRACT_SOURCES
    )
    abstract_disagreement: dict[str, Any] | None = None
    if incoming_has_abstract and not existing_has_abstract:
        abstract = incoming.abstract
        abstract_source = incoming.abstract_source
    elif incoming_has_abstract and existing.abstract != incoming.abstract:
        # Two real abstracts for one work. The stored one stays canonical — a quote has
        # very likely already been substring-checked against it, and promoting a later
        # reading would move ground the verifier already stood on. But the alternative is
        # kept rather than refused: it came from a real response, it is what ADR-006's
        # chain may well prefer on the next resolve, and losing it is the one thing this
        # store is not allowed to do.
        abstract_disagreement = {
            "field": "abstract",
            "stored": existing.abstract,
            "offered": incoming.abstract,
            "stored_source": existing.abstract_source.value,
            "offered_source": incoming.abstract_source.value,
            "offered_by": incoming.provenance.provider,
            "external_url": incoming.provenance.external_url,
            "retrieved_at": incoming.provenance.retrieved_at,
        }

    if violations:
        raise AppendOnlyViolation(
            f"source_id {existing.source_id!r} is already stored and this write would "
            "change identity or provenance-bearing values, which the append-only store "
            "does not permit: " + "; ".join(violations)
        )

    disagreements = [
        {
            "field": key,
            "stored": stored,
            "offered": offered,
            "offered_by": incoming.provenance.provider,
            "external_url": incoming.provenance.external_url,
            "retrieved_at": incoming.provenance.retrieved_at,
        }
        for key, stored, offered in differing
        if key not in _IDENTITY_CSL_FIELDS
    ]
    if abstract_disagreement is not None:
        disagreements.append(abstract_disagreement)

    nothing_added = merged_csl == existing.csl and abstract == existing.abstract
    if nothing_added and not disagreements:
        return MergeResult(None, [])

    return MergeResult(
        SourceRecord(
            source_id=existing.source_id,
            csl=merged_csl,
            # The provenance of the *enriching* call — it is the response that justifies
            # the new field or the alternative reading. The original stays on the earlier
            # version, which is never deleted.
            provenance=incoming.provenance,
            abstract=abstract,
            abstract_source=abstract_source,
        ),
        disagreements,
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
    disagreements: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    """Where this version's provider described a field differently from the stored value
    (ADR-028). The stored value stayed canonical; this is the alternative reading, kept
    with the provenance that offered it. Empty for a plain enrichment."""
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
        #: Alternative readings recorded rather than raised on (ADR-028). Counted so a
        #: run that quietly disagrees with itself on every reference is visible.
        self.disagreements = 0

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
            merged = _merge_append_only(existing, record)
            if merged.is_noop:
                self.no_ops += 1
                self._index_record(existing)
                return existing.source_id

            enriched = merged.record or existing
            session.add(
                _to_row(
                    enriched,
                    version=rows[0].version + 1,
                    disagreements=merged.disagreements,
                )
            )
            self.enrichments += 1
            if merged.disagreements:
                self.disagreements += len(merged.disagreements)
                log.info(
                    "source %s: %s described %s differently; stored value kept canonical "
                    "and the alternative recorded on version %d (ADR-028)",
                    enriched.source_id,
                    record.provenance.provider,
                    ", ".join(d["field"] for d in merged.disagreements),
                    rows[0].version + 1,
                )
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
            found = latest.get(source_id)
            if found is None:
                self._index_absent(source_id)
            else:
                self._index_record(found.to_record())

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
        self._disagreements_by_id: dict[str, list[dict]] = {}

    def disagreements_for(self, source_id: str) -> list[dict]:
        """The alternative readings recorded against this source (ADR-028)."""
        return list(self._disagreements_by_id.get(source_id, []))

    async def put(self, record: SourceRecord) -> str:
        _assert_provider_caller(3)
        _assert_real_provenance(record)

        versions = self._versions.get(record.source_id)
        if not versions:
            self._versions[record.source_id] = [record]
            self.writes += 1
            self._index_record(record)
            return record.source_id

        merged = _merge_append_only(versions[-1], record)
        if merged.is_noop:
            self.no_ops += 1
            self._index_record(versions[-1])
            return record.source_id

        enriched = merged.record or versions[-1]
        versions.append(enriched)
        self.enrichments += 1
        self.disagreements += len(merged.disagreements)
        self._disagreements_by_id.setdefault(record.source_id, []).extend(merged.disagreements)
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


def _to_row(
    record: SourceRecord, *, version: int, disagreements: list[dict] | None = None
) -> SourceStoreRow:
    return SourceStoreRow(
        source_id=record.source_id,
        version=version,
        csl=record.csl,
        provenance=record.provenance.model_dump(),
        abstract=record.abstract,
        abstract_source=record.abstract_source.value,
        disagreements=disagreements or [],
        written_at=utcnow(),
    )
