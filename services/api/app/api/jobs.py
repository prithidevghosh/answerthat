"""Background jobs on arq/Redis.

Two things run long enough that holding a request open for them would be a lie about what
the system is doing:

* **ingest + arbitration** — GROBID, then a Crossref/S2/OpenAlex round trip per reference
  at roughly one request per second (ADR-001, ADR-010).
* **review** — claim-first retrieval and verification, 5–8 minutes on a 40-claim paper, a
  floor set by the rate limit rather than by our code (ADR-014).

Both return a `job_id` immediately. Progress is visible: ingest through
`/parse-status`, review through the SSE stream's `progress` events.

A job that fails records its failure. It does not leave the document in `running` forever,
and it does not mark itself `complete` with nothing in it (HR-3).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("app.api.jobs")

INGEST_QUEUE = "answerthat:ingest"
REVIEW_QUEUE = "answerthat:review"

JOB_TIMEOUT_SECONDS = 60 * 30
MAX_TRIES = 2


async def ingest_document(ctx: dict, doc_id: str, filename: str | None, payload: bytes) -> dict:
    """Owned by B1's pipeline; this is the queue-side entry point.

    Kept thin on purpose — the parsing cascade belongs to B1 and B3 must not reimplement
    any part of it here.
    """
    services = ctx["services"]
    pipeline = services.require("ingest")
    try:
        result = await pipeline.run(doc_id=doc_id, filename=filename, payload=payload)
    except Exception as exc:  # noqa: BLE001 — recorded then re-raised, never swallowed
        log.exception("ingest failed for %s", doc_id)
        await pipeline.record_failure(doc_id, f"{type(exc).__name__}: {exc}")
        raise
    log.info("ingest complete for %s → version %s", doc_id, result.get("version"))
    return result


async def run_review(ctx: dict, doc_id: str, section_ids: list[str] | None = None) -> dict:
    """Owned by B2's review pipeline; this is the queue-side entry point."""
    services = ctx["services"]
    runner = services.require("review")
    try:
        return await runner.run(doc_id=doc_id, section_ids=section_ids)
    except Exception as exc:  # noqa: BLE001 — recorded then re-raised, never swallowed
        log.exception("review failed for %s", doc_id)
        await runner.record_failure(doc_id, f"{type(exc).__name__}: {exc}")
        raise


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

    functions = [ingest_document, run_review]
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
