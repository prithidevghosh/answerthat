"""Background work → conversation. ADR-033.

Parsing and review run as background asyncio tasks and know nothing about any chat. This
module is the bridge, one watcher per conversation, and the distinction it exists to
maintain is between two very different kinds of thing that happen in a background job:

**Progress is a UI event.** A parse ticking from `references` to `arbiter`, a review
verifying its eleventh claim — these go straight to the event stream as `progress`, and
**no agent turn runs**. Running one per tick would spend the document's token budget
narrating a progress bar, and would produce a chat full of "still going!" that nobody
asked for.

**A state transition is a conversation.** Parse complete, parse failed, review complete,
review failed: a `system_notice` carrying the facts is appended, and a turn runs. The
model writes the sentence the researcher reads. The notice states everything and
instructs nothing — what to *do* about a completed parse (announce it, summarise the
counts, offer the full result rather than dumping it) is standing policy in the system
prompt, and putting it in the notice instead would make the agent a template engine with
extra steps.

Watchers are idempotent and self-terminating: each one polls or subscribes until the work
it is watching reaches a terminal state, emits its notice once, and stops.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from app.orchestrator.index import EvidenceKind
from app.orchestrator.session import Conversation

log = logging.getLogger("app.orchestrator.watcher")

__all__ = ["ConversationWatcher"]


class ConversationWatcher:
    """Watches one document's background work on behalf of one conversation."""

    def __init__(
        self,
        *,
        orchestrator: Any,
        ingest: Any,
        review: Any,
        documents: Any,
        sources: Any,
        index: Any,
        settings: Any,
    ) -> None:
        self._orchestrator = orchestrator
        self._ingest = ingest
        self._review = review
        self._documents = documents
        self._sources = sources
        self._index = index
        self._settings = settings
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------ lifecycle

    def watch_parse(self, conversation: Conversation) -> None:
        self._spawn(f"parse:{conversation.conversation_id}", self._watch_parse(conversation))

    def watch_review(self, conversation: Conversation) -> None:
        self._spawn(f"review:{conversation.conversation_id}", self._watch_review(conversation))

    def stop(self, conversation_id: str) -> None:
        for key in [k for k in self._tasks if k.endswith(f":{conversation_id}")]:
            task = self._tasks.pop(key, None)
            if task is not None and not task.done():
                task.cancel()

    def _spawn(self, key: str, coro: Any) -> None:
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            # Already watching. A second watcher would emit every notice twice, and the
            # agent would tell the user their parse finished two conversations running.
            coro.close()
            return
        task = asyncio.create_task(coro)
        self._tasks[key] = task

        def _forget(_task: asyncio.Task, watched: str = key) -> None:
            self._tasks.pop(watched, None)

        task.add_done_callback(_forget)

    # ------------------------------------------------------------------ parsing

    async def _watch_parse(self, conversation: Conversation) -> None:
        doc_id = conversation.doc_id
        store = self._orchestrator.conversations
        last: tuple[Any, Any] | None = None
        interval = self._settings.orchestrator_watch_interval_s

        try:
            while True:
                status = await _maybe_await(self._ingest.status(doc_id)) or {}
                if not status:
                    return
                state, stage = status.get("state"), status.get("stage")

                if (state, stage) != last:
                    last = (state, stage)
                    # UI only. No agent turn — see the module docstring.
                    await store.append_event(
                        conversation,
                        "progress",
                        {
                            "kind": "parse",
                            "state": state,
                            "stage": stage,
                            "fraction": status.get("progress"),
                            "elapsed_s": status.get("elapsed_s"),
                        },
                    )

                if state == "complete":
                    await self._on_parse_complete(conversation, status)
                    return
                if state == "failed":
                    await self._orchestrator.notify(
                        conversation,
                        "parse_failed",
                        doc_id=doc_id,
                        stage=stage,
                        error=status.get("error"),
                    )
                    return
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — logged with its traceback, never silent
            log.exception("parse watcher failed for conversation %s", conversation.conversation_id)

    async def _on_parse_complete(self, conversation: Conversation, status: dict) -> None:
        doc_id = conversation.doc_id
        counts: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            report = await _maybe_await(self._ingest.parse_report(doc_id))
            counts = report.get("counts", {}) if report else {}

        # Kicked off before the notice so the index is building while the model composes
        # its message, rather than after the user has already asked a question about it.
        await self._index_spans(doc_id)

        await self._orchestrator.notify(
            conversation,
            "parse_complete",
            doc_id=doc_id,
            version=status.get("version"),
            elapsed_s=status.get("elapsed_s"),
            **{key: counts.get(key) for key in (
                "total_detected",
                "resolved",
                "parsed_unresolved",
                "low_confidence",
                "quarantined",
                "orphan_marker",
            )},
        )

    # ------------------------------------------------------------------ review

    async def _wait_for_review(self, doc_id: str, *, not_job: str | None) -> str:
        """Block until a review job exists for this document, and return its id.

        `not_job` is the job already watched to completion. A user who asks for a second
        review gets a new `job_id`, and waiting for *a different* one is what lets this
        watcher follow the new job without re-announcing the old one — `stream()` replays
        a finished job's whole log, terminal event included, so re-subscribing to the same
        job would tell the user their review had finished a second time.

        The watcher is started when the conversation is opened, and at that moment there
        is almost never a review — the user has to be told the plan and agree to it first,
        which takes at least two turns. `ReviewJobRunner.stream` raises for a document
        whose review was never started (deliberately: an empty stream is indistinguishable
        from a review that found nothing), so a watcher that subscribed immediately died
        on the spot and every finding the review later produced went unforwarded.

        Waiting is the fix, rather than re-spawning the watcher from the `start_review`
        tool. The tool would have to reach the watcher, the watcher reaches the
        orchestrator, and the orchestrator owns the tools — a cycle, to make correctness
        depend on the order two things happened in. A bridge that waits for the work to
        appear has no ordering to get wrong.

        The poll is a dictionary lookup in the same process, not I/O.
        """
        from app.api.adapters import maybe_await  # noqa: PLC0415

        while True:
            status = await maybe_await(self._review.status(doc_id)) or {}
            job_id = status.get("job_id") or ""
            if status.get("status", "not_started") != "not_started" and job_id != not_job:
                return job_id
            await asyncio.sleep(self._settings.orchestrator_watch_interval_s)

    async def _watch_review(self, conversation: Conversation) -> None:
        """Follow this document's reviews for as long as the conversation lives.

        A loop rather than a single pass, because a review is not a once-per-document
        event: the user may ask for a second one over a narrower scope, or re-run a
        completed one, and each deserves the same progress and the same completion notice.
        """
        watched: str | None = None
        try:
            while True:
                watched = await self._watch_one_review(conversation, not_job=watched)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — logged with its traceback, never silent
            log.exception("review watcher failed for conversation %s", conversation.conversation_id)

    async def _watch_one_review(self, conversation: Conversation, *, not_job: str | None) -> str:
        doc_id = conversation.doc_id
        store = self._orchestrator.conversations
        findings: list[dict] = []

        job_id = await self._wait_for_review(doc_id, not_job=not_job)
        try:
            async for name, payload in self._review.stream(doc_id):
                if name == "heartbeat":
                    continue
                if name == "finding":
                    findings.append(payload)
                    # Nested, not splatted. A `Finding` has its own `kind` field
                    # (`missing_work`, `claim_citation_mismatch`), and splatting it over
                    # the envelope overwrote the `parse`/`review` discriminator the
                    # frontend switches on — twenty-four finding events arrived labelled
                    # `kind: "missing_work"` and were invisible to a client filtering for
                    # review progress. Nesting makes the collision impossible rather than
                    # ordering the keys so that it happens not to occur.
                    await store.append_event(
                        conversation,
                        "progress",
                        {"kind": "review", "event": "finding", "finding": payload},
                    )
                    continue
                if name == "progress":
                    await store.append_event(
                        conversation,
                        "progress",
                        {"kind": "review", "event": "progress", "stats": payload},
                    )
                    continue
                if name == "complete":
                    await self._index_review_output(doc_id, findings)
                    await self._orchestrator.notify(
                        conversation,
                        "review_complete",
                        doc_id=doc_id,
                        **{
                            key: payload.get(key)
                            for key in (
                                "verified",
                                "total",
                                "findings",
                                "candidates_considered",
                                "quote_check_failures",
                                "unverifiable_no_abstract",
                                "claims_without_candidates",
                            )
                        },
                    )
                    return job_id
                if name == "error":
                    await self._orchestrator.notify(
                        conversation,
                        "review_failed",
                        doc_id=doc_id,
                        error=payload.get("message") or payload.get("detail") or "unknown",
                    )
                    return job_id
        except KeyError:
            # The job vanished between the status check and the subscribe — only possible
            # if the runner was reset underneath us. Nothing to report; the outer loop
            # goes back to waiting.
            return job_id
        return job_id

    # ------------------------------------------------------------------ indexing

    async def _index_spans(self, doc_id: str) -> None:
        """Index the paper's own sentences once the parse has persisted them."""
        document = await self._documents.get(doc_id)
        if document is None:
            return
        texts: list[tuple[EvidenceKind, str, str]] = [
            ("span", span.id, span.text)
            for section in document.sections
            for block in section.blocks
            for span in block.spans
            if span.text.strip()
        ]
        if texts:
            self._index.schedule(doc_id, texts)

    async def _index_review_output(self, doc_id: str, findings: list[dict]) -> None:
        """Index the review's claims, findings and the abstracts it fetched.

        Abstracts come from the source store rather than from the finding payload: the
        payload carries the verbatim quote, which is one sentence, and "what else is in
        this reference?" is a question about the whole abstract.
        """
        texts: list[tuple[EvidenceKind, str, str]] = []
        seen_claims: set[str] = set()
        source_ids: list[str] = []

        for finding in findings:
            claim = finding.get("claim") or {}
            claim_id = claim.get("claim_id")
            if claim_id and claim_id not in seen_claims and claim.get("text"):
                seen_claims.add(claim_id)
                texts.append(("claim", claim_id, claim["text"]))

            finding_id = finding.get("finding_id")
            verification = finding.get("verification") or {}
            blurb = " ".join(
                part
                for part in (claim.get("text"), verification.get("label"), verification.get("quote"))
                if part
            )
            if finding_id and blurb.strip():
                texts.append(("finding", finding_id, blurb))

            source_id = finding.get("source_id")
            if source_id:
                source_ids.append(source_id)

        if source_ids:
            unique = list(dict.fromkeys(source_ids))
            with contextlib.suppress(Exception):
                await self._sources.warm(unique)
                for source_id in unique:
                    record = self._sources.get(source_id)
                    if record is not None and record.abstract:
                        texts.append(("abstract", source_id, record.abstract))

        if texts:
            self._index.schedule(doc_id, texts)


async def _maybe_await(value: Any) -> Any:
    from app.api.adapters import maybe_await  # noqa: PLC0415 — interop shim, not a port

    return await maybe_await(value)
