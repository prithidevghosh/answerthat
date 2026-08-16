"""Copy-on-write versioned persistence for the Document IR.

Every accepted change writes a **new version**; no version is ever mutated or deleted.
That gives three things the rest of the system depends on:

* every edit is revertible (CP-6), because the previous version is still there;
* diffs are structural rather than textual, because we are comparing two typed trees;
* an audit view is possible at all — "what did the citation multiset look like before
  the agent touched it" has an answer.

Reverting appends a new version whose content equals an old one. It does not rewind the
history. A user who reverts and then changes their mind must be able to get back.

Two implementations, same protocol: `InMemoryDocumentStore` (tests, and the only one
that runs without Postgres) and `PostgresDocumentStore`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel
from sqlalchemy import Integer, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.contracts import Document
from app.core.db import Base, utcnow
from app.core.errors import IRVersionConflict

__all__ = [
    "VersionInfo",
    "DocumentStore",
    "InMemoryDocumentStore",
    "PostgresDocumentStore",
    "DocumentVersionRow",
]

FIRST_VERSION = 1


class VersionInfo(BaseModel):
    doc_id: str
    version: int
    parent_version: int | None
    label: str
    created_at: str


@runtime_checkable
class DocumentStore(Protocol):
    """Append-only, copy-on-write. There is deliberately no `update` and no `delete`."""

    async def create(self, doc: Document, *, label: str = "ingest") -> Document: ...
    async def commit(self, doc: Document, *, parent_version: int, label: str) -> Document: ...
    async def get(self, doc_id: str, version: int) -> Document | None: ...
    async def head(self, doc_id: str) -> Document | None: ...
    async def head_version(self, doc_id: str) -> int | None: ...
    async def history(self, doc_id: str) -> list[VersionInfo]: ...
    async def revert(self, doc_id: str, to_version: int, *, label: str = "") -> Document: ...


def _frozen_copy(doc: Document, version: int) -> Document:
    """A deep copy at a given version. The caller's object is never adopted by the store.

    Copy-on-write only works if the store owns its snapshots. Holding a reference to a
    document someone else can still mutate would let history change retroactively.
    """
    copy = doc.model_copy(deep=True)
    copy.version = version
    return copy


class _StoreBase:
    """Shared version arithmetic, so the two backends cannot disagree about it."""

    async def head_version(self, doc_id: str) -> int | None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def _next_version(self, doc_id: str, parent_version: int) -> int:
        current = await self.head_version(doc_id)
        if current is None:
            raise IRVersionConflict(
                f"document {doc_id!r} has no versions; call create() before commit()"
            )
        if parent_version != current:
            raise IRVersionConflict(
                f"document {doc_id!r} is at version {current}, but the change was computed "
                f"against version {parent_version}. Re-read the head and re-apply — do not "
                f"overwrite: the intervening version is someone's accepted edit."
            )
        return current + 1


class InMemoryDocumentStore(_StoreBase):
    """Process-local store. Used by unit tests and by nothing that must survive a restart."""

    def __init__(self) -> None:
        self._versions: dict[str, dict[int, Document]] = {}
        self._meta: dict[str, list[VersionInfo]] = {}

    async def create(self, doc: Document, *, label: str = "ingest") -> Document:
        if doc.doc_id in self._versions:
            raise IRVersionConflict(f"document {doc.doc_id!r} already exists; use commit()")
        stored = _frozen_copy(doc, FIRST_VERSION)
        self._versions[doc.doc_id] = {FIRST_VERSION: stored}
        self._meta[doc.doc_id] = [
            VersionInfo(
                doc_id=doc.doc_id,
                version=FIRST_VERSION,
                parent_version=None,
                label=label,
                created_at=utcnow().isoformat(),
            )
        ]
        return stored.model_copy(deep=True)

    async def commit(self, doc: Document, *, parent_version: int, label: str) -> Document:
        version = await self._next_version(doc.doc_id, parent_version)
        stored = _frozen_copy(doc, version)
        self._versions[doc.doc_id][version] = stored
        self._meta[doc.doc_id].append(
            VersionInfo(
                doc_id=doc.doc_id,
                version=version,
                parent_version=parent_version,
                label=label,
                created_at=utcnow().isoformat(),
            )
        )
        return stored.model_copy(deep=True)

    async def get(self, doc_id: str, version: int) -> Document | None:
        found = self._versions.get(doc_id, {}).get(version)
        return found.model_copy(deep=True) if found else None

    async def head(self, doc_id: str) -> Document | None:
        version = await self.head_version(doc_id)
        return None if version is None else await self.get(doc_id, version)

    async def head_version(self, doc_id: str) -> int | None:
        versions = self._versions.get(doc_id)
        return max(versions) if versions else None

    async def history(self, doc_id: str) -> list[VersionInfo]:
        return list(self._meta.get(doc_id, []))

    async def revert(self, doc_id: str, to_version: int, *, label: str = "") -> Document:
        return await _revert(self, doc_id, to_version, label)


class DocumentVersionRow(Base):
    """One row per version. Insert-only — there is no UPDATE path to this table."""

    # ADR-020: B1's tables are `ir_*`. No migrations in v1, so a table created under the
    # wrong name is not something a later revision fixes — it is a `make db-reset`.
    __tablename__ = "ir_document_versions"

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(Text)
    doc: Mapped[dict] = mapped_column(JSONB)


class PostgresDocumentStore(_StoreBase):
    """Durable store. One session per store instance, supplied by the caller."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, doc: Document, *, label: str = "ingest") -> Document:
        stored = _frozen_copy(doc, FIRST_VERSION)
        self._session.add(
            DocumentVersionRow(
                doc_id=stored.doc_id,
                version=FIRST_VERSION,
                parent_version=None,
                label=label,
                created_at=utcnow().isoformat(),
                doc=stored.model_dump(mode="json"),
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IRVersionConflict(
                f"document {doc.doc_id!r} already exists; use commit()"
            ) from exc
        return stored

    async def commit(self, doc: Document, *, parent_version: int, label: str) -> Document:
        version = await self._next_version(doc.doc_id, parent_version)
        stored = _frozen_copy(doc, version)
        self._session.add(
            DocumentVersionRow(
                doc_id=stored.doc_id,
                version=version,
                parent_version=parent_version,
                label=label,
                created_at=utcnow().isoformat(),
                doc=stored.model_dump(mode="json"),
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Someone else committed the same version number between our read and our
            # write. Surface it; do not retry silently onto a moved head.
            await self._session.rollback()
            raise IRVersionConflict(
                f"version {version} of document {doc.doc_id!r} was written concurrently"
            ) from exc
        return stored

    async def get(self, doc_id: str, version: int) -> Document | None:
        row = await self._session.get(DocumentVersionRow, (doc_id, version))
        return Document.model_validate(row.doc) if row else None

    async def head(self, doc_id: str) -> Document | None:
        version = await self.head_version(doc_id)
        return None if version is None else await self.get(doc_id, version)

    async def head_version(self, doc_id: str) -> int | None:
        stmt = select(DocumentVersionRow.version).where(DocumentVersionRow.doc_id == doc_id)
        return max((await self._session.scalars(stmt)).all(), default=None)

    async def history(self, doc_id: str) -> list[VersionInfo]:
        stmt = (
            select(DocumentVersionRow)
            .where(DocumentVersionRow.doc_id == doc_id)
            .order_by(DocumentVersionRow.version)
        )
        return [
            VersionInfo(
                doc_id=row.doc_id,
                version=row.version,
                parent_version=row.parent_version,
                label=row.label,
                created_at=row.created_at,
            )
            for row in (await self._session.scalars(stmt)).all()
        ]

    async def revert(self, doc_id: str, to_version: int, *, label: str = "") -> Document:
        return await _revert(self, doc_id, to_version, label)


async def _revert(store: DocumentStore, doc_id: str, to_version: int, label: str) -> Document:
    """Append a new version whose content equals `to_version`.

    History is never truncated: reverting is itself an edit, and un-reverting has to be
    possible.
    """
    target = await store.get(doc_id, to_version)
    if target is None:
        raise IRVersionConflict(f"document {doc_id!r} has no version {to_version}")
    head_version = await store.head_version(doc_id)
    assert head_version is not None  # get() succeeded, so at least one version exists
    return await store.commit(
        target,
        parent_version=head_version,
        label=label or f"revert to v{to_version}",
    )
