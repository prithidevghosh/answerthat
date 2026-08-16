"""Background jobs on arq/Redis.

Two things run long enough that holding a request open for them would be a lie about what
the system is doing:

* **ingest + arbitration** — GROBID, then a Crossref/S2/OpenAlex round trip per reference
  at roughly one request per second (ADR-001, ADR-010).
* **review** — claim-first retrieval and verification, 5–8 minutes on a 40-claim paper, a
  floor set by the rate limit rather than by our code (ADR-014).

Both return a `job_id` immediately. Progress is visible three ways: the `agent_jobs` row
(`GET /api/jobs/{job_id}`), ingest's `/parse-status`, and review's SSE `progress` events.

A job that fails records its failure into `agent_jobs` and then re-raises. It does not
leave the document in `running` forever, and it does not mark itself `complete` with
nothing in it (HR-3). A worker killed outright cannot record anything, which is why
`jobstore` reports a job that has stopped reporting as failed rather than as running
(ADR-022).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

log = logging.getLogger("app.api.jobs")

INGEST_QUEUE = "answerthat:ingest"
REVIEW_QUEUE = "answerthat:review"

JOB_TIMEOUT_SECONDS = 60 * 30
MAX_TRIES = 2


async def _jobs(services: Any) -> Any:
    """The `agent_jobs` store, or None when the worker booted without one.

    None rather than a raise: the job row is bookkeeping, and refusing to run the actual
    ingest because we cannot write a status row would turn a reporting gap into a total
    outage. It is logged loudly instead.
    """
    store = getattr(services, "jobs", None)
    if store is None:
        log.error("no agent_jobs store is bound; this job's status will not be visible")
    return store


async def ingest_document(
    ctx: dict, doc_id: str, filename: str | None, payload: bytes, job_id: str = ""
) -> dict:
    """Owned by B1's pipeline; this is the queue-side entry point.

    Kept thin on purpose — the parsing cascade belongs to B1 and B3 must not reimplement
    any part of it here. What B3 owns is the status of the attempt.
    """
    services = ctx["services"]
    pipeline = services.require("ingest")
    jobs = await _jobs(services)
    if jobs and job_id:
        await jobs.start(job_id)
    try:
        result = await pipeline.run(doc_id=doc_id, filename=filename, payload=payload)
    except Exception as exc:  # noqa: BLE001 — recorded then re-raised, never swallowed
        log.exception("ingest failed for %s", doc_id)
        reason = f"{type(exc).__name__}: {exc}"
        if jobs and job_id:
            await jobs.fail(job_id, reason)
        await pipeline.record_failure(doc_id, reason)
        raise
    if jobs and job_id:
        await jobs.succeed(job_id)
    log.info("ingest complete for %s → version %s", doc_id, result.get("version"))
    return result


async def run_review(
    ctx: dict, doc_id: str, section_ids: list[str] | None = None, job_id: str = ""
) -> dict:
    """Owned by B2's review pipeline; this is the queue-side entry point."""
    services = ctx["services"]
    runner = services.require("review")
    jobs = await _jobs(services)
    if jobs and job_id:
        await jobs.start(job_id)
    try:
        result = await runner.run(doc_id=doc_id, section_ids=section_ids)
    except Exception as exc:  # noqa: BLE001 — recorded then re-raised, never swallowed
        log.exception("review failed for %s", doc_id)
        reason = f"{type(exc).__name__}: {exc}"
        if jobs and job_id:
            await jobs.fail(job_id, reason)
        await runner.record_failure(doc_id, reason)
        raise
    if jobs and job_id:
        await jobs.succeed(job_id)
    return result


async def startup(ctx: dict) -> None:
    """The worker boots through the same composition root as the API — including HR-2.

    A worker that came up without the keys the API refuses to start without would be a
    second, quieter path to the degraded mode ADR-010 forbids.
    """
    from app.api.deps import build_services  # noqa: PLC0415

    ctx["services"] = build_services()
    log.info("arq worker ready")


async def shutdown(ctx: dict) -> None:
    services = ctx.get("services")
    closer = getattr(services, "aclose", None)
    if closer is not None:
        await closer()


def redis_settings(settings: Any = None):
    from arq.connections import RedisSettings  # noqa: PLC0415

    url = getattr(settings, "redis_url", None) if settings else None
    return RedisSettings.from_dsn(url) if url else RedisSettings()


class WorkerSettings:
    """`arq app.api.jobs.WorkerSettings`"""

    functions: ClassVar[list] = [ingest_document, run_review]
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = JOB_TIMEOUT_SECONDS
    max_tries = MAX_TRIES
    keep_result = 60 * 60

    @staticmethod
    def redis_settings():  # noqa: D102
        return redis_settings()


__all__ = [
    "INGEST_QUEUE",
    "REVIEW_QUEUE",
    "WorkerSettings",
    "ingest_document",
    "run_review",
]
