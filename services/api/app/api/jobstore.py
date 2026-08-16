"""`agent_jobs` — background work with a first-class status. ADR-022, HR-3.

The jobs table matters more than it looks. Ingest takes minutes and review takes five to
eight on a forty-claim paper, so both return a `job_id` immediately. The moment that is
true, **a worker that dies is a result the user never gets told about**: the UI streams
nothing forever, and a reviewer reads "nothing arrived" as "nothing was found". That is the
same false negative as ADR-010, reached from a different direction.

So three things are recorded, not two:

* `succeeded` — the work finished and its output is real.
* `failed` — the work stopped and here is why. The worker records this itself, in the
  `except` block that then re-raises.
* **a running job nobody has touched for longer than the job timeout is reported as
  failed.** This is the SIGKILL case, and it is the only one the worker cannot report on
  its own behalf. Inferring it here is not a guess dressed as a fact: the message says
  plainly that the worker stopped reporting, which is exactly what we know.

Table prefix is `agent_` per ADR-020, and there is no Alembic in v1 — `create_all()` at
startup, `make db-reset` to wipe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel
from sqlalchemy import Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.contracts import Job, JobStatus
from app.core.db import Base, utcnow

log = logging.getLogger("app.api.jobstore")

JobKind = Literal["ingest", "review"]

# How long a `running` job may go without a heartbeat before it is reported as failed.
# Generous on purpose: a slow GROBID parse is not a dead worker, and calling a live job
# dead would be its own kind of dishonesty.
DEFAULT_STALE_AFTER_SECONDS = 60 * 35


class JobView(BaseModel):
    """Appendix A's `Job`, plus what a UI needs to route the user back to the work."""

    job_id: str
    kind: JobKind
    doc_id: str
    status: JobStatus
    progress_current: int = 0
    progress_total: int = 0
    error: str | None = None
    upload_path: str | None = None
    """Where the uploaded PDF was written (ADR-022). Recorded so a failed ingest can be
    retried from the bytes rather than from the researcher."""
    created_at: str
    updated_at: str

    def as_contract(self) -> Job:
        return Job(
            job_id=self.job_id,
            kind=self.kind,
            status=self.status,
            progress_current=self.progress_current,
            progress_total=self.progress_total,
            error=self.error,
        )


class JobStore(Protocol):
    async def create(
        self, *, job_id: str, kind: JobKind, doc_id: str, upload_path: str | None = None
    ) -> JobView: ...
    async def start(self, job_id: str) -> JobView | None: ...
    async def progress(self, job_id: str, current: int, total: int) -> JobView | None: ...
    async def succeed(self, job_id: str) -> JobView | None: ...
    async def fail(self, job_id: str, error: str) -> JobView | None: ...
    async def get(self, job_id: str) -> JobView | None: ...
    async def for_document(self, doc_id: str, kind: JobKind | None = None) -> list[JobView]: ...


class AgentJobRow(Base):
    __tablename__ = "agent_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


def _stale(view: JobView, stale_after_seconds: int) -> JobView:
    """Report a silent `running` job as failed, saying exactly what we know.

    A worker killed mid-job cannot write its own failure, and leaving the row in `running`
    forever would render in the UI as a review still in progress — indistinguishable from
    a slow one, and eventually read as a clean paper (HR-3).
    """
    if view.status is not JobStatus.RUNNING:
        return view
    try:
        last = datetime.fromisoformat(view.updated_at)
    except ValueError:  # a malformed timestamp is not evidence of health either
        return view
    if utcnow() - last <= timedelta(seconds=stale_after_seconds):
        return view

    minutes = stale_after_seconds // 60
    return view.model_copy(
        update={
            "status": JobStatus.FAILED,
            "error": (
                f"the worker running this {view.kind} job stopped reporting more than "
                f"{minutes} minutes ago and is presumed dead. What you see is partial. "
                f"Re-run it — this is not a result, and it is not 'nothing found'."
            ),
        }
    )


class PostgresJobStore:
    """One session per unit of work — a job row is written from the API process and from
    the arq worker, and neither may hold a transaction open across unrelated work."""

    def __init__(self, session_scope, *, stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS):
        self._session_scope = session_scope
        self._stale_after = stale_after_seconds

    async def create(
        self, *, job_id: str, kind: JobKind, doc_id: str, upload_path: str | None = None
    ) -> JobView:
        now = utcnow().isoformat()
        async with self._session_scope() as session:
            session.add(
                AgentJobRow(
                    job_id=job_id,
                    kind=kind,
                    doc_id=doc_id,
                    status=JobStatus.QUEUED.value,
                    upload_path=upload_path,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
        return JobView(
            job_id=job_id,
            kind=kind,
            doc_id=doc_id,
            status=JobStatus.QUEUED,
            upload_path=upload_path,
            created_at=now,
            updated_at=now,
        )

    async def start(self, job_id: str) -> JobView | None:
        return await self._update(job_id, status=JobStatus.RUNNING)

    async def progress(self, job_id: str, current: int, total: int) -> JobView | None:
        return await self._update(
            job_id, status=JobStatus.RUNNING, progress_current=current, progress_total=total
        )

    async def succeed(self, job_id: str) -> JobView | None:
        return await self._update(job_id, status=JobStatus.SUCCEEDED)

    async def fail(self, job_id: str, error: str) -> JobView | None:
        log.warning("job %s failed: %s", job_id, error)
        return await self._update(job_id, status=JobStatus.FAILED, error=error)

    async def get(self, job_id: str) -> JobView | None:
        async with self._session_scope() as session:
            row = await session.get(AgentJobRow, job_id)
            view = _view(row) if row else None
        return _stale(view, self._stale_after) if view else None

    async def for_document(self, doc_id: str, kind: JobKind | None = None) -> list[JobView]:
        stmt = select(AgentJobRow).where(AgentJobRow.doc_id == doc_id)
        if kind is not None:
            stmt = stmt.where(AgentJobRow.kind == kind)
        async with self._session_scope() as session:
            rows = (await session.scalars(stmt)).all()
        views = [_stale(_view(row), self._stale_after) for row in rows]
        return sorted(views, key=lambda v: v.created_at)

    async def _update(self, job_id: str, **fields) -> JobView | None:
        async with self._session_scope() as session:
            row = await session.get(AgentJobRow, job_id)
            if row is None:
                # Not silently created: a status update for a job nobody started means the
                # caller and this table disagree about what is running, and inventing the
                # row would hide that.
                log.error("status update for unknown job %s: %s", job_id, fields)
                return None
            for name, value in fields.items():
                setattr(row, name, value.value if isinstance(value, JobStatus) else value)
            row.updated_at = utcnow().isoformat()
            await session.flush()
            return _view(row)


class InMemoryJobStore:
    """Process-local, for tests and for anything that must run without Postgres."""

    def __init__(self, *, stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS) -> None:
        self._jobs: dict[str, JobView] = {}
        self._stale_after = stale_after_seconds

    async def create(
        self, *, job_id: str, kind: JobKind, doc_id: str, upload_path: str | None = None
    ) -> JobView:
        now = utcnow().isoformat()
        view = JobView(
            job_id=job_id,
            kind=kind,
            doc_id=doc_id,
            status=JobStatus.QUEUED,
            upload_path=upload_path,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job_id] = view
        return view

    async def start(self, job_id: str) -> JobView | None:
        return self._update(job_id, status=JobStatus.RUNNING)

    async def progress(self, job_id: str, current: int, total: int) -> JobView | None:
        return self._update(
            job_id, status=JobStatus.RUNNING, progress_current=current, progress_total=total
        )

    async def succeed(self, job_id: str) -> JobView | None:
        return self._update(job_id, status=JobStatus.SUCCEEDED)

    async def fail(self, job_id: str, error: str) -> JobView | None:
        return self._update(job_id, status=JobStatus.FAILED, error=error)

    async def get(self, job_id: str) -> JobView | None:
        view = self._jobs.get(job_id)
        return _stale(view, self._stale_after) if view else None

    async def for_document(self, doc_id: str, kind: JobKind | None = None) -> list[JobView]:
        return sorted(
            (
                _stale(v, self._stale_after)
                for v in self._jobs.values()
                if v.doc_id == doc_id and (kind is None or v.kind == kind)
            ),
            key=lambda v: v.created_at,
        )

    def _update(self, job_id: str, **fields) -> JobView | None:
        view = self._jobs.get(job_id)
        if view is None:
            log.error("status update for unknown job %s: %s", job_id, fields)
            return None
        updated = view.model_copy(update={**fields, "updated_at": utcnow().isoformat()})
        self._jobs[job_id] = updated
        return updated


def _view(row: AgentJobRow) -> JobView:
    return JobView(
        job_id=row.job_id,
        kind=row.kind,  # type: ignore[arg-type]
        doc_id=row.doc_id,
        status=JobStatus(row.status),
        progress_current=row.progress_current or 0,
        progress_total=row.progress_total or 0,
        error=row.error,
        upload_path=row.upload_path,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


__all__ = [
    "DEFAULT_STALE_AFTER_SECONDS",
    "AgentJobRow",
    "InMemoryJobStore",
    "JobKind",
    "JobStore",
    "JobView",
    "PostgresJobStore",
]
