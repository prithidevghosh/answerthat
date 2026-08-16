"""Persisted parse reports. ADR-032.

The parse report — every reference with its tier, every orphan marker, the reconciliation
notes, the counts — was until now held only in the in-process `IngestRegistry`. That was a
defensible scope for the job *state* around an ingest, and an indefensible one for the
report itself: after an API restart, `/api/documents/{id}/parse` raised `ParseFailure` for
every document ingested before it, and the README carried the 404 as a known limitation.

Two things changed the calculus. The obvious one is that a conversation now outlives the
process (`app/orchestrator/session.py`), so a persisted chat about an unpersisted parse is
a conversation the agent cannot continue — it would come back after a restart able to read
its own history and unable to answer a single question about the paper it is discussing.
The less obvious one is that the report is *derived, immutable data about a specific
document version*: it is exactly the shape that belongs in a table, and the only reason it
was not in one is that nothing had needed it to survive.

Insert-only, keyed by `(doc_id, version)`, mirroring `app/ir/store.py`. A re-ingest that
produces version 2 writes a second row rather than overwriting the first, so the report
you read always describes the version you asked about.

The registry stays the primary read. It holds the live `IngestResult` — richer than the
JSON, and correct for an ingest still in flight — and this table is the fallback for when
it does not. That order matters: preferring the table would serve a stale report for a
document being re-ingested right now.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from sqlalchemy import Integer, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow

__all__ = [
    "ParseReportRow",
    "ParseReportStore",
    "PostgresParseReportStore",
    "InMemoryParseReportStore",
]

log = logging.getLogger("app.parsing.reports")


class ParseReportRow(Base):
    """`parse_reports` — insert-only, one row per (document, version)."""

    __tablename__ = "parse_reports"

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    report: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[str] = mapped_column(Text)


class ParseReportStore(Protocol):
    async def put(self, doc_id: str, version: int, report: dict[str, Any]) -> None: ...
    async def get(self, doc_id: str, version: int | None = None) -> dict[str, Any] | None:
        """The report for a version, or the highest-versioned one when `version` is None."""
        ...


class PostgresParseReportStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def put(self, doc_id: str, version: int, report: dict[str, Any]) -> None:
        existing = await self._session.get(ParseReportRow, (doc_id, version))
        if existing is not None:
            # Insert-only: a report for a version that already has one is the same parse
            # of the same bytes. Rewriting it would let a later, differently-configured
            # run silently restate what an earlier version's bibliography looked like.
            return
        self._session.add(
            ParseReportRow(
                doc_id=doc_id,
                version=version,
                report=report,
                created_at=utcnow().isoformat(),
            )
        )
        await self._session.flush()

    async def get(self, doc_id: str, version: int | None = None) -> dict[str, Any] | None:
        if version is not None:
            row = await self._session.get(ParseReportRow, (doc_id, version))
            return dict(row.report) if row is not None else None
        stmt = (
            select(ParseReportRow)
            .where(ParseReportRow.doc_id == doc_id)
            .order_by(ParseReportRow.version.desc())
            .limit(1)
        )
        row = (await self._session.scalars(stmt)).first()
        return dict(row.report) if row is not None else None


class InMemoryParseReportStore:
    """Process-local store, for tests and for anything that runs without Postgres."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, int], dict[str, Any]] = {}

    async def put(self, doc_id: str, version: int, report: dict[str, Any]) -> None:
        self._rows.setdefault((doc_id, version), report)

    async def get(self, doc_id: str, version: int | None = None) -> dict[str, Any] | None:
        if version is not None:
            return self._rows.get((doc_id, version))
        versions = [v for (did, v) in self._rows if did == doc_id]
        if not versions:
            return None
        return self._rows[(doc_id, max(versions))]
