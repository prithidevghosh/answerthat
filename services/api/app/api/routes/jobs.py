"""Job status, one shape for both kinds of background work. ADR-022, HR-3.

There are two sources of truth for a running job and they answer different questions.
The owning pipeline — B1's ingest, B2's review — knows what stage it is at and how many
claims it has verified. The `agent_jobs` row knows whether anyone is still working on it.

This route reconciles them, and the precedence is deliberate: **a live owner status wins
while the owner is alive, and the row wins when the owner has gone quiet.** A worker that
was killed leaves the owner's in-process state frozen at whatever it last was, which
renders as a review still in progress — indistinguishable from a slow one, and eventually
read by the user as a paper with nothing wrong with it. That is the false negative ADR-010
names, and the staleness rule in `jobstore` is what turns it into a visible failure.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.adapters import maybe_await
from app.api.deps import Services
from app.api.jobstore import JobView
from app.core.contracts import JobStatus

router = APIRouter(prefix="/api", tags=["jobs"])

# The owning pipelines speak in their own vocabulary. Mapped once, here.
_OWNER_STATES: dict[str, JobStatus] = {
    "queued": JobStatus.QUEUED,
    "running": JobStatus.RUNNING,
    "complete": JobStatus.SUCCEEDED,
    "succeeded": JobStatus.SUCCEEDED,
    "failed": JobStatus.FAILED,
}


def services(request: Request) -> Services:
    return request.app.state.services


@router.get("/jobs/{job_id}", response_model=JobView)
async def get_job(request: Request, job_id: str) -> JobView:
    svc = services(request)
    view = await svc.require("jobs").get(job_id)
    if view is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no job {job_id!r}. Jobs are recorded when they are enqueued, so an unknown "
                f"id means the work was never started — not that it finished with nothing."
            ),
        )
    return await reconcile(svc, view)


@router.get("/documents/{doc_id}/jobs", response_model=list[JobView])
async def list_jobs(request: Request, doc_id: str) -> list[JobView]:
    svc = services(request)
    views = await svc.require("jobs").for_document(doc_id)
    return [await reconcile(svc, view) for view in views]


async def reconcile(svc: Services, view: JobView) -> JobView:
    """Fold the owning pipeline's live status into the job row.

    A row already reported `failed` — including by the staleness rule — is left alone. The
    owner cannot argue a dead worker back to life, and letting a frozen in-process status
    override "the worker stopped reporting" would restore exactly the silence this exists
    to break.
    """
    if view.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
        return view

    owner = svc.review if view.kind == "review" else svc.ingest
    if owner is None:
        return view

    payload = await maybe_await(owner.status(view.doc_id))
    if not isinstance(payload, dict):
        return view

    raw = str(payload.get("state") or payload.get("status") or "").lower()
    status = _OWNER_STATES.get(raw)
    if status is None:
        return view

    return view.model_copy(
        update={
            "status": status,
            "error": payload.get("error") or view.error,
            "progress_current": _int(payload, "verified", "progress_current", default=view.progress_current),
            "progress_total": _int(payload, "total", "progress_total", default=view.progress_total),
        }
    )


def _int(payload: dict[str, Any], *names: str, default: int) -> int:
    for name in names:
        value = payload.get(name)
        if isinstance(value, int):
            return value
    return default


__all__ = ["reconcile", "router"]
