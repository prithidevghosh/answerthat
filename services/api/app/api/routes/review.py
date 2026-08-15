"""Review: start a background job, then stream its findings over SSE.

**The browser connects to this endpoint directly.** Do not proxy it through a Next.js route
handler — the handler buffers the stream and makes it look broken. This is noted for F1 in
`memory.md` §5 and it is not a style preference; it is the difference between findings
appearing as they verify and a six-minute blank screen.

Ordering is citability-descending (ADR-014), so the first thirty seconds carry the most
value, and every event carries `verified / total` so an in-progress review can never be
mistaken for a finished one.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import Services
from app.api.schemas import JobAccepted, ReviewRequest

log = logging.getLogger("app.api.review")
router = APIRouter(prefix="/api/documents", tags=["review"])

HEARTBEAT_SECONDS = 15


def services(request: Request) -> Services:
    return request.app.state.services


@router.post("/{doc_id}/review", response_model=JobAccepted, status_code=202)
async def start_review(request: Request, doc_id: str, payload: ReviewRequest | None = None) -> JobAccepted:
    svc = services(request)
    runner = svc.require("review")
    section_ids = payload.section_ids if payload else None

    job_id = await runner.start(doc_id=doc_id, section_ids=section_ids)
    return JobAccepted(
        job_id=job_id,
        doc_id=doc_id,
        poll=f"/api/documents/{doc_id}/review/status",
        stream=f"/api/documents/{doc_id}/review/stream",
    )


@router.get("/{doc_id}/review/status")
async def review_status(request: Request, doc_id: str) -> dict:
    svc = services(request)
    status = await svc.require("review").status(doc_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"no review job for document {doc_id!r}")
    return status


@router.get("/{doc_id}/review/stream")
async def stream_review(request: Request, doc_id: str) -> EventSourceResponse:
    """Server-sent events. Event names:

    * `finding`   — a `Finding` from Appendix A, highest citability first
    * `progress`  — `{verified, total}`
    * `error`     — a failure, named and shown (HR-3); the stream then closes
    * `complete`  — `{verified, total, findings}`
    * `heartbeat` — keeps intermediaries from closing an idle connection

    A stream that ends without `complete` ended badly, and the client should say so rather
    than presenting a partial review as a finished one.
    """
    svc = services(request)
    runner = svc.require("review")

    async def events() -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        finished = asyncio.Event()

        async def pump() -> None:
            try:
                async for event_name, payload in runner.stream(doc_id):
                    await queue.put({"event": event_name, "data": json.dumps(payload, default=str)})
            except Exception as exc:  # noqa: BLE001 — reported to the client, never silent
                log.exception("review stream failed for %s", doc_id)
                await queue.put(
                    {
                        "event": "error",
                        "data": json.dumps(
                            {
                                "error": type(exc).__name__,
                                "detail": str(exc),
                                "note": "the review stopped here; what you see above is partial",
                            }
                        ),
                    }
                )
            finally:
                finished.set()

        task = asyncio.create_task(pump())
        try:
            while not (finished.is_set() and queue.empty()):
                if await request.is_disconnected():
                    log.info("client disconnected from review stream for %s", doc_id)
                    break
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
        finally:
            task.cancel()

    return EventSourceResponse(
        events(),
        # nginx and friends will happily buffer an event stream into uselessness.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


__all__ = ["router"]
