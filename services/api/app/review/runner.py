"""Background review jobs and their replayable event stream. ADR-014.

`ReviewRunner` in `stream.py` is a one-shot async generator: iterate it and you get the
findings. That is the right shape for the pipeline and the wrong shape for an HTTP
surface, because a review runs for five to eight minutes and a browser will disconnect
inside that window. This module is the difference.

**Events are buffered, so the stream is replayable.** A client that connects late — or
reconnects after a dropped SSE connection — gets every event from the beginning, then
follows live. Without that, a reconnect halfway through a six-minute review silently
loses the findings that already arrived, which is the same "partial coverage presented as
complete" failure ADR-014's progress counter exists to prevent, just arriving over the
network instead of from the pipeline.

Event names are `finding`, `progress`, `complete`, `error` — the ones B3 frames into SSE.

In-process asyncio tasks, one per document. That is the right scope for a single API
worker and for the demo; if we ever run several, this becomes an `arq` job whose events
land in Redis, and neither `start()` nor `stream()` changes shape.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

from app.core.config import Settings

log = logging.getLogger("app.review.runner")

__all__ = ["ReviewJob", "ReviewJobRunner", "get_review_runner"]

JobStatus = Literal["queued", "running", "complete", "failed"]

#: Sent when a job has produced nothing for this long, so an idle SSE connection is not
#: mistaken by a proxy for a dead one. B3 frames it; it is never buffered.
HEARTBEAT_SECONDS = 15.0


class ReviewJob:
    """One document's review: its event log, its status, and its subscribers."""

    def __init__(
        self, job_id: str, doc_id: str, section_ids: list[str] | None = None
    ) -> None:
        self.job_id = job_id
        self.doc_id = doc_id
        #: What this job was asked to cover, so a later request for a *different* scope
        #: is not answered with this job's findings.
        self.section_ids = section_ids
        self.status: JobStatus = "queued"
        self.error: str | None = None
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.latest_progress: dict[str, Any] = {}
        self.task: asyncio.Task | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def emit(self, name: str, payload: dict[str, Any]) -> None:
        """Append to the log and fan out to live subscribers."""
        async with self._lock:
            self.events.append((name, payload))
            if name in ("progress", "complete"):
                # The terminal event carries the *final* counters, so it updates status
                # too — otherwise `status()` reports the second-to-last progress tick
                # forever after the job finishes, which reads as a stalled review.
                self.latest_progress = payload
            subscribers = list(self._subscribers)
        for queue in subscribers:
            queue.put_nowait((name, payload))

    async def subscribe(self) -> tuple[list[tuple[str, dict[str, Any]]], asyncio.Queue]:
        """Snapshot the log and attach a live queue, atomically.

        Both under the same lock on purpose: snapshotting first and subscribing second
        would drop any event emitted in between, and doing it the other way round would
        duplicate one. Neither is acceptable in a feed a researcher reads as a complete
        list of findings.
        """
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            backlog = list(self.events)
            self._subscribers.add(queue)
        return backlog, queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    @property
    def finished(self) -> bool:
        return self.status in ("complete", "failed")

    def status_payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "doc_id": self.doc_id,
            "status": self.status,
            "error": self.error,
            "events_emitted": len(self.events),
            **self.latest_progress,
        }


class ReviewJobRunner:
    """Starts, tracks, and streams review jobs."""

    def __init__(self, review_runner_factory: Callable[[], Any]) -> None:
        self._factory = review_runner_factory
        self._jobs: dict[str, ReviewJob] = {}

    # ------------------------------------------------------------------ lifecycle

    def start(
        self, doc_id: str, section_ids: list[str] | None = None, *, force: bool = False
    ) -> str:
        """Launch the review in the background and return its `job_id`.

        Re-starting a document whose review is still running returns the existing
        `job_id` rather than racing a second pipeline against the same rate limiter.

        A *completed* review of the same scope is also returned rather than re-run. The
        job keeps its whole event log and `subscribe()` replays it, so the feed repopulates
        for free. Without this, the review screen — which auto-starts on mount — billed a
        fresh pass over the entire paper on every page load and every refresh, discarding
        findings the user had already read. `force=True` is the deliberate re-run.

        A failed job is never replayed: the user asked for a review and got an error, and
        handing them that same error forever instead of retrying is not a cache, it is a
        dead end. Same for a different `section_ids` scope, which is a different question.
        """
        existing = self._jobs.get(doc_id)
        if existing is not None and not existing.finished:
            return existing.job_id
        if (
            existing is not None
            and not force
            and existing.status == "complete"
            and existing.section_ids == section_ids
        ):
            return existing.job_id

        job = ReviewJob(
            job_id=f"rev_{uuid.uuid4().hex[:16]}", doc_id=doc_id, section_ids=section_ids
        )
        self._jobs[doc_id] = job
        job.task = asyncio.create_task(self._run_job(job, section_ids))
        return job.job_id

    async def run(self, doc_id: str, section_ids: list[str] | None = None) -> ReviewJob:
        """Run to completion inline. For workers and tests, not for a request handler."""
        job = ReviewJob(job_id=f"rev_{uuid.uuid4().hex[:16]}", doc_id=doc_id)
        self._jobs[doc_id] = job
        await self._run_job(job, section_ids)
        return job

    async def _run_job(self, job: ReviewJob, section_ids: list[str] | None) -> None:
        job.status = "running"
        try:
            runner = self._factory()
            async for name, payload in runner.stream(job.doc_id, section_ids=section_ids):
                # `stream.py` says "done"; the wire says "complete".
                await job.emit("complete" if name == "done" else name, payload)
            job.status = "complete"
        except asyncio.CancelledError:
            await self.record_failure(job.doc_id, "review cancelled")
            raise
        except Exception as exc:
            # HR-3: the failure becomes a visible terminal state, not a stream that
            # simply stops. A truncated feed with no error looks like a finished review
            # that found less than it should have.
            #
            # Logged with its traceback as well as sent: this is the only exception
            # handler between the pipeline and the browser, and the user-facing string is
            # one line by design. `AttributeError: 'str' object has no attribute 'get'`
            # is an honest terminal state and a useless bug report — the operator needs
            # the frame it came from, and nothing else in the process was going to print it.
            log.exception("review job %s failed for document %s", job.job_id, job.doc_id)
            await self.record_failure(job.doc_id, f"{type(exc).__name__}: {exc}")

    async def record_failure(self, doc_id: str, message: str) -> None:
        """Mark a job failed and put the reason on its stream."""
        job = self._jobs.get(doc_id)
        if job is None:
            job = ReviewJob(job_id=f"rev_{uuid.uuid4().hex[:16]}", doc_id=doc_id)
            self._jobs[doc_id] = job
        job.status = "failed"
        job.error = message
        await job.emit("error", {"message": message, **job.latest_progress})

    # ------------------------------------------------------------------ reading

    def status(self, doc_id: str) -> dict[str, Any]:
        job = self._jobs.get(doc_id)
        if job is None:
            return {"doc_id": doc_id, "status": "not_started", "events_emitted": 0}
        return job.status_payload()

    async def stream(
        self, doc_id: str, *, heartbeat_seconds: float = HEARTBEAT_SECONDS
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Replay this document's events, then follow live until the job ends.

        Raises `KeyError` for a document whose review was never started — an empty
        stream would be indistinguishable from a review that found nothing.
        """
        job = self._jobs.get(doc_id)
        if job is None:
            raise KeyError(
                f"no review has been started for document {doc_id!r}. Call start() first "
                "— an empty stream would read as a review that found nothing."
            )

        backlog, queue = await job.subscribe()
        try:
            for event in backlog:
                yield event
            # A job that finished before we subscribed has already been fully replayed.
            if job.finished:
                return
            while True:
                try:
                    name, payload = await asyncio.wait_for(queue.get(), heartbeat_seconds)
                except TimeoutError:
                    yield ("heartbeat", job.latest_progress or {"doc_id": doc_id})
                    continue
                yield (name, payload)
                if name in ("complete", "error"):
                    return
        finally:
            await job.unsubscribe(queue)


_runner: ReviewJobRunner | None = None


def get_review_runner(
    settings: Settings | None = None,
    *,
    review_runner_factory: Callable[[], Any] | None = None,
) -> ReviewJobRunner:
    """Process-wide job runner.

    `review_runner_factory` builds the per-job pipeline. It is injected rather than
    constructed here because the pipeline needs B1's `DocumentStore`, which the API's
    composition root owns — this module must not reach into `app/ir/`.
    """
    global _runner
    if _runner is None:
        if review_runner_factory is None:
            raise RuntimeError(
                "get_review_runner() needs a review_runner_factory on first call: the "
                "pipeline requires B1's DocumentStore, which app/review must not import "
                "directly. Bind it once in the API composition root."
            )
        _runner = ReviewJobRunner(review_runner_factory)
    return _runner


def reset() -> None:
    """Tests only."""
    global _runner
    _runner = None
