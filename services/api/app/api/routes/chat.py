"""The agentic flow's HTTP surface: one conversation per document, streamed over SSE.

**The browser connects to this endpoint directly.** Do not proxy it through a Next.js
route handler — the handler buffers the stream, and the agent's text arrives in one clump
at the end, which is worse than no streaming at all. This is the same lesson `review.py`
carries and it costs more here: a review stream that clumps delivers findings late, and a
chat stream that clumps looks like the agent is not working.

The shape follows `review.py` deliberately, down to the headers. Two differences worth
knowing:

* `POST /messages` returns `202` immediately and runs the turn in the background. A turn
  can call a tool that starts a six-minute review; holding the request open for it would
  tie the answer to one HTTP connection surviving.
* The stream replays from the persisted event log rather than from an in-memory buffer, so
  a browser refresh mid-review repaints the whole conversation and then follows live.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import Services
from app.api.schemas import ChatAccepted, ChatMessageRequest, ConversationHandle
from app.orchestrator.session import ConversationNotFound

log = logging.getLogger("app.api.chat")
router = APIRouter(prefix="/api", tags=["chat"])

HEARTBEAT_SECONDS = 15


def services(request: Request) -> Services:
    return request.app.state.services


def _handle(conversation_id: str, doc_id: str) -> ConversationHandle:
    return ConversationHandle(
        conversation_id=conversation_id,
        doc_id=doc_id,
        stream=f"/api/chat/{conversation_id}/stream",
        poll=f"/api/chat/{conversation_id}",
    )


async def _conversation(svc: Services, conversation_id: str):
    orchestrator = svc.require("orchestrator")
    try:
        return orchestrator, await orchestrator.conversations.get(conversation_id)
    except ConversationNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"{exc.args[0] if exc.args else conversation_id}. Start one with "
                "POST /api/documents/{doc_id}/chat."
            ),
        ) from exc


@router.post("/documents/{doc_id}/chat", response_model=ConversationHandle)
async def start_conversation(request: Request, doc_id: str) -> ConversationHandle:
    """Create or return this document's conversation.

    One per document, and returning the existing one is not a convenience: a second
    conversation about the same paper would split the record of what the researcher was
    told about it, and neither half would be the transcript.

    The watchers are started here rather than on the first message, because the interesting
    case is a user who lands on the chat screen while their PDF is still parsing — the
    agent has to be able to announce the parse finishing whether or not they have typed
    anything yet.
    """
    svc = services(request)
    orchestrator = svc.require("orchestrator")
    watcher = svc.require("watcher")

    conversation, created = await orchestrator.conversations.start(doc_id)
    watcher.watch_parse(conversation)
    watcher.watch_review(conversation)

    log.info(
        "%s conversation %s for document %s",
        "created" if created else "resumed",
        conversation.conversation_id,
        doc_id,
    )
    return _handle(conversation.conversation_id, conversation.doc_id)


@router.get("/chat/{conversation_id}")
async def get_conversation(request: Request, conversation_id: str) -> dict:
    """The persisted message log, for a cold page load."""
    _orchestrator, conversation = await _conversation(services(request), conversation_id)
    return {
        "conversation_id": conversation.conversation_id,
        "doc_id": conversation.doc_id,
        "status": conversation.status,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "messages": [
            message.as_dict()
            for message in conversation.messages
            # `tool` messages are the model's working notes; the UI renders tool activity
            # from the `tool_call`/`tool_result` events, which carry the structured payload
            # a card needs. Serving both would double every tool line in the transcript.
            if message.role != "tool"
        ],
        "events": [event.as_dict() for event in conversation.events],
    }


@router.post("/chat/{conversation_id}/messages", response_model=ChatAccepted, status_code=202)
async def send_message(
    request: Request, conversation_id: str, payload: ChatMessageRequest
) -> ChatAccepted:
    """Send a user message. Returns immediately; the turn runs in the background."""
    orchestrator, conversation = await _conversation(services(request), conversation_id)
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="an empty message has nothing to answer")

    await orchestrator.send_user_message(conversation, payload.text)
    return ChatAccepted(
        conversation_id=conversation_id,
        stream=f"/api/chat/{conversation_id}/stream",
    )


@router.post("/chat/{conversation_id}/stop")
async def stop_turn(request: Request, conversation_id: str) -> dict:
    """Cancel the in-flight turn. The conversation stays usable.

    A six-minute agent turn the user cannot interrupt is a trap, so this is not optional
    chrome. Cancelling emits a terminal `error` event for the turn — never for the
    conversation — and anything already committed stays committed, because a commit is a
    finished database write by the time this could reach it.
    """
    orchestrator, conversation = await _conversation(services(request), conversation_id)
    stopped = await orchestrator.stop(conversation)
    return {
        "conversation_id": conversation_id,
        "stopped": stopped,
        "detail": (
            "The turn was cancelled." if stopped else "No turn was running; nothing to cancel."
        ),
    }


@router.get("/chat/{conversation_id}/stream")
async def stream_chat(request: Request, conversation_id: str) -> EventSourceResponse:
    """Server-sent events. Replays the event log, then follows live.

    Event names, which the frontend is written against:

    * `message_start` — `{message_id, role}`
    * `message_delta` — `{message_id, text}`
    * `message`       — the complete message
    * `tool_call`     — `{call_id, name, arguments, label}`
    * `tool_result`   — `{call_id, name, ok, summary, data}`
    * `progress`      — `{kind: "parse" | "review", ...}` from the watcher
    * `awaiting_confirmation` — `{kind, proposal}`, the structured change set or manifest
    * `error`         — terminal for the turn, never for the conversation
    * `done`          — `{message_id, tokens_used, budget_remaining}`
    * `heartbeat`     — every 15s, so an idle connection is not closed by an intermediary

    Unlike the review stream, this one does not end: a conversation has no terminal state,
    so the generator runs until the client disconnects.
    """
    _orchestrator, conversation = await _conversation(services(request), conversation_id)

    async def events() -> AsyncIterator[dict]:
        backlog, queue = await conversation.subscribe()
        try:
            for event in backlog:
                yield {"event": event.event, "data": json.dumps(event.payload, default=str)}
            while True:
                if await request.is_disconnected():
                    log.info("client disconnected from chat stream %s", conversation_id)
                    return
                try:
                    live = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue
                yield {"event": live.event, "data": json.dumps(live.payload, default=str)}
        finally:
            await conversation.unsubscribe(queue)

    return EventSourceResponse(
        events(),
        # nginx and friends will happily buffer an event stream into uselessness.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


__all__ = ["router"]
