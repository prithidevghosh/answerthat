"""Anchor context fingerprints, in a side table. ADR-017.

`CitationAnchor` carries a `fingerprint_id`, never the vector. Two reasons, and the
second is the real one:

* With copy-on-write versioning (ADR-004) an inline vector would be duplicated into every
  document version.
* **Structural diffs are how a user verifies HR-5 with their own eyes.** A diff in which
  every citation anchor renders as 512 floats is unreadable, and the entire approval flow
  depends on that diff being human-scannable.

Fingerprints are **immutable and shared across versions**. An anchor's recorded context
does not change because an unrelated paragraph was edited, so the same `fingerprint_id`
is valid in every version that contains that anchor. Re-embedding a sentence produces a
new row with a new id rather than overwriting an old one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Integer, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow
from app.ir import ids

__all__ = [
    "Fingerprint",
    "FingerprintStore",
    "InMemoryFingerprintStore",
    "PostgresFingerprintStore",
    "AnchorFingerprintRow",
    "new_fingerprint_id",
]

FINGERPRINT = "fp"


def new_fingerprint_id() -> str:
    return ids.new_id(FINGERPRINT)


@dataclass(frozen=True)
class Fingerprint:
    """One embedded sentence: the vector plus the text it came from.

    The text is kept alongside the vector on purpose. Without it a fingerprint is an
    opaque 512-float blob that nobody can debug, and "why did this anchor reattach
    there?" becomes unanswerable.
    """

    fingerprint_id: str
    vector: list[float]
    text: str
    model: str
    dimensions: int


class FingerprintStore(Protocol):
    async def put(self, fingerprint: Fingerprint) -> str: ...
    async def get(self, fingerprint_id: str) -> Fingerprint | None: ...
    async def get_many(self, fingerprint_ids: list[str]) -> dict[str, Fingerprint]: ...


class AnchorFingerprintRow(Base):
    """`anchor_fingerprints` — insert-only, shared across document versions."""

    __tablename__ = "anchor_fingerprints"

    fingerprint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    vector: Mapped[list] = mapped_column(JSONB)
    text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))
    dimensions: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text)


class InMemoryFingerprintStore:
    """Process-local store, for tests and for anything that must run without Postgres."""

    def __init__(self) -> None:
        self._by_id: dict[str, Fingerprint] = {}

    async def put(self, fingerprint: Fingerprint) -> str:
        # Immutable: re-putting the same id is a no-op rather than an overwrite, so a
        # fingerprint referenced by an older version can never change under it.
        self._by_id.setdefault(fingerprint.fingerprint_id, fingerprint)
        return fingerprint.fingerprint_id

    async def get(self, fingerprint_id: str) -> Fingerprint | None:
        return self._by_id.get(fingerprint_id)

    async def get_many(self, fingerprint_ids: list[str]) -> dict[str, Fingerprint]:
        return {fid: fp for fid in fingerprint_ids if (fp := self._by_id.get(fid)) is not None}


class PostgresFingerprintStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def put(self, fingerprint: Fingerprint) -> str:
        existing = await self._session.get(AnchorFingerprintRow, fingerprint.fingerprint_id)
        if existing is not None:
            return fingerprint.fingerprint_id
        self._session.add(
            AnchorFingerprintRow(
                fingerprint_id=fingerprint.fingerprint_id,
                vector=list(fingerprint.vector),
                text=fingerprint.text,
                model=fingerprint.model,
                dimensions=fingerprint.dimensions,
                created_at=utcnow().isoformat(),
            )
        )
        await self._session.flush()
        return fingerprint.fingerprint_id

    async def get(self, fingerprint_id: str) -> Fingerprint | None:
        row = await self._session.get(AnchorFingerprintRow, fingerprint_id)
        return _to_fingerprint(row) if row else None

    async def get_many(self, fingerprint_ids: list[str]) -> dict[str, Fingerprint]:
        if not fingerprint_ids:
            return {}
        stmt = select(AnchorFingerprintRow).where(
            AnchorFingerprintRow.fingerprint_id.in_(fingerprint_ids)
        )
        rows = (await self._session.scalars(stmt)).all()
        return {row.fingerprint_id: _to_fingerprint(row) for row in rows}


def _to_fingerprint(row: AnchorFingerprintRow) -> Fingerprint:
    return Fingerprint(
        fingerprint_id=row.fingerprint_id,
        vector=list(row.vector),
        text=row.text,
        model=row.model,
        dimensions=row.dimensions,
    )
