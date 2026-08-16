"""Conversation persistence. ADR-032.

A conversation about a paper is not a proposal. `app/agent/store.py` keeps change sets in
memory and argues, correctly, that losing one costs the user a re-issued command. The
same argument fails here: a chat is the *record* of what the researcher asked and what the
agent told them, and it accumulates over the six-to-eight minutes a review takes plus
however long the editing conversation runs. Losing it to a restart would lose the
reasoning behind an edit the user is midway through approving.

Three tables, all append-only:

* `chat_conversations` — one per (document, conversation).
* `chat_messages` — the model's view. What gets replayed into `converse()`.
* `chat_events` — the browser's view. What gets replayed into a reconnecting `EventSource`.

**Two logs rather than one, deliberately.** They are not the same data at different
resolutions: a `message_delta` is a rendering event with no place in the model's context,
and a `role="tool"` message carries a payload the UI renders as a card rather than as
text. Deriving either from the other means a lossy transform running on every read, and
the failure mode is a refresh that repaints a conversation subtly unlike the one the user
was looking at. Storage is cheap; a transcript that changes when you reload it is not.

Sequence numbers are per-conversation and assigned under the same lock that appends, so
an event log read at any moment is a prefix of the log read later — the property the SSE
replay depends on to hand a reconnecting client the full backlog and then live events
with nothing dropped or duplicated.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy import Integer, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow
from app.ir import ids

log = logging.getLogger("app.orchestrator.session")

__all__ = [
    "ChatConversationRow",
    "ChatEventRow",
    "ChatMessageRow",
    "Conversation",
    "ConversationNotFound",
    "ConversationStore",
    "InMemoryConversationStore",
    "Message",
    "PostgresConversationStore",
    "RecordedEvent",
    "MessageRole",
]

MessageRole = Literal["user", "assistant", "tool", "system_notice"]


# --------------------------------------------------------------------------- rows


class ChatConversationRow(Base):
    __tablename__ = "chat_conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(Text)


class ChatEventRow(Base):
    __tablename__ = "chat_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[str] = mapped_column(Text)


# --------------------------------------------------------------------------- values


@dataclass
class Message:
    message_id: str
    conversation_id: str
    seq: int
    role: MessageRole
    content: str = ""
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "seq": self.seq,
            "role": self.role,
            "content": self.content,
            "tool_calls": self.tool_calls,
            "tool_call_id": self.tool_call_id,
            "created_at": self.created_at,
        }

    def as_openai(self) -> dict[str, Any]:
        """This message in the shape `converse()` wants.

        `system_notice` becomes a `user` message rather than a `system` one. A second
        system message mid-conversation competes with the standing prompt for authority,
        and OpenAI's own guidance is that later system turns are weakly honoured. The
        notice is information arriving from outside the conversation, which is what a user
        turn *is* — and the prompt already says the model composes the sentence.
        """
        if self.role == "assistant":
            payload: dict[str, Any] = {"role": "assistant", "content": self.content or None}
            if self.tool_calls:
                payload["tool_calls"] = self.tool_calls
            return payload
        if self.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": self.tool_call_id or "",
                "content": self.content,
            }
        if self.role == "system_notice":
            return {"role": "user", "content": f"[system notice]\n{self.content}"}
        return {"role": "user", "content": self.content}


@dataclass
class RecordedEvent:
    seq: int
    event: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "event": self.event, "payload": self.payload}


@dataclass
class Conversation:
    """One conversation, plus its live subscribers.

    The subscriber machinery is modelled on `ReviewJob` in `app/review/runner.py` and for
    the same reason: **snapshot and subscribe happen under one lock**. Snapshotting first
    and subscribing second drops any event emitted in between; the other order duplicates
    one. Neither is acceptable in a transcript a researcher reads as a complete record of
    what they were told.
    """

    conversation_id: str
    doc_id: str
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""
    messages: list[Message] = field(default_factory=list)
    events: list[RecordedEvent] = field(default_factory=list)
    _subscribers: set[asyncio.Queue] = field(default_factory=set, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def next_message_seq(self) -> int:
        return len(self.messages)

    def next_event_seq(self) -> int:
        return len(self.events)

    async def subscribe(self) -> tuple[list[RecordedEvent], asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            backlog = list(self.events)
            self._subscribers.add(queue)
        return backlog, queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def fan_out(self, event: RecordedEvent) -> None:
        async with self._lock:
            self.events.append(event)
            subscribers = list(self._subscribers)
        for queue in subscribers:
            queue.put_nowait(event)

    def openai_messages(self) -> list[dict[str, Any]]:
        return [message.as_openai() for message in self.messages]


class ConversationNotFound(KeyError):
    """Asked for a conversation that does not exist. A 404 with a reason, never an empty
    transcript the caller might render as "you have not said anything yet"."""


# --------------------------------------------------------------------------- stores


class ConversationRowStore(Protocol):
    async def create(self, conversation_id: str, doc_id: str) -> None: ...
    async def load(self, conversation_id: str) -> dict | None: ...
    async def find_for_document(self, doc_id: str) -> str | None: ...
    async def append_message(self, message: Message) -> None: ...
    async def append_event(self, conversation_id: str, event: RecordedEvent) -> None: ...
    async def touch(self, conversation_id: str, status: str) -> None: ...


class PostgresConversationRowStore:
    def __init__(self, session_scope: Any) -> None:
        self._session_scope = session_scope

    async def create(self, conversation_id: str, doc_id: str) -> None:
        now = utcnow().isoformat()
        async with self._session_scope() as session:
            session.add(
                ChatConversationRow(
                    conversation_id=conversation_id,
                    doc_id=doc_id,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()

    async def load(self, conversation_id: str) -> dict | None:
        async with self._session_scope() as session:
            head = await session.get(ChatConversationRow, conversation_id)
            if head is None:
                return None
            messages = (
                await session.scalars(
                    select(ChatMessageRow)
                    .where(ChatMessageRow.conversation_id == conversation_id)
                    .order_by(ChatMessageRow.seq)
                )
            ).all()
            events = (
                await session.scalars(
                    select(ChatEventRow)
                    .where(ChatEventRow.conversation_id == conversation_id)
                    .order_by(ChatEventRow.seq)
                )
            ).all()
            return {
                "conversation_id": head.conversation_id,
                "doc_id": head.doc_id,
                "status": head.status,
                "created_at": head.created_at,
                "updated_at": head.updated_at,
                "messages": [
                    Message(
                        message_id=row.message_id,
                        conversation_id=row.conversation_id,
                        seq=row.seq,
                        role=row.role,
                        content=row.content or "",
                        tool_calls=row.tool_calls,
                        tool_call_id=row.tool_call_id,
                        created_at=row.created_at,
                    )
                    for row in messages
                ],
                "events": [
                    RecordedEvent(seq=row.seq, event=row.event, payload=dict(row.payload))
                    for row in events
                ],
            }

    async def find_for_document(self, doc_id: str) -> str | None:
        async with self._session_scope() as session:
            stmt = (
                select(ChatConversationRow.conversation_id)
                .where(ChatConversationRow.doc_id == doc_id)
                .order_by(ChatConversationRow.created_at.desc())
                .limit(1)
            )
            return (await session.scalars(stmt)).first()

    async def append_message(self, message: Message) -> None:
        async with self._session_scope() as session:
            session.add(
                ChatMessageRow(
                    message_id=message.message_id,
                    conversation_id=message.conversation_id,
                    seq=message.seq,
                    role=message.role,
                    content=message.content,
                    tool_calls=message.tool_calls,
                    tool_call_id=message.tool_call_id,
                    created_at=message.created_at,
                )
            )
            await session.flush()

    async def append_event(self, conversation_id: str, event: RecordedEvent) -> None:
        async with self._session_scope() as session:
            session.add(
                ChatEventRow(
                    event_id=ids.new_id("evt"),
                    conversation_id=conversation_id,
                    seq=event.seq,
                    event=event.event,
                    payload=event.payload,
                    created_at=utcnow().isoformat(),
                )
            )
            await session.flush()

    async def touch(self, conversation_id: str, status: str) -> None:
        async with self._session_scope() as session:
            row = await session.get(ChatConversationRow, conversation_id)
            if row is None:
                return
            row.status = status
            row.updated_at = utcnow().isoformat()
            await session.flush()


class InMemoryConversationRowStore:
    """Process-local rows, for tests and for anything running without Postgres."""

    def __init__(self) -> None:
        self._heads: dict[str, dict] = {}
        self._messages: dict[str, list[Message]] = {}
        self._events: dict[str, list[RecordedEvent]] = {}

    async def create(self, conversation_id: str, doc_id: str) -> None:
        now = utcnow().isoformat()
        self._heads[conversation_id] = {
            "conversation_id": conversation_id,
            "doc_id": doc_id,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        self._messages[conversation_id] = []
        self._events[conversation_id] = []

    async def load(self, conversation_id: str) -> dict | None:
        head = self._heads.get(conversation_id)
        if head is None:
            return None
        return {
            **head,
            "messages": list(self._messages.get(conversation_id, [])),
            "events": list(self._events.get(conversation_id, [])),
        }

    async def find_for_document(self, doc_id: str) -> str | None:
        for conversation_id, head in self._heads.items():
            if head["doc_id"] == doc_id:
                return conversation_id
        return None

    async def append_message(self, message: Message) -> None:
        self._messages.setdefault(message.conversation_id, []).append(message)

    async def append_event(self, conversation_id: str, event: RecordedEvent) -> None:
        self._events.setdefault(conversation_id, []).append(event)

    async def touch(self, conversation_id: str, status: str) -> None:
        head = self._heads.get(conversation_id)
        if head is not None:
            head["status"] = status
            head["updated_at"] = utcnow().isoformat()


class ConversationStore:
    """Conversations, held in memory for the live path and durable underneath.

    The in-memory `Conversation` objects are where subscribers live and where a turn
    reads its message history; the row store is where everything lands so a restart or a
    second process can rebuild them. `get()` rehydrates from rows on a miss, which is what
    makes "restart the API, the conversation reloads with its full history" true rather
    than aspirational.
    """

    def __init__(self, rows: ConversationRowStore) -> None:
        self._rows = rows
        self._live: dict[str, Conversation] = {}
        self._create_lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle

    async def start(self, doc_id: str) -> tuple[Conversation, bool]:
        """The conversation for this document, creating one if there is none.

        Returns `(conversation, created)`. One conversation per document: the chat screen
        is about a paper, and a second conversation for the same paper would silently
        split the record of what the researcher was told about it.
        """
        async with self._create_lock:
            existing_id = await self._rows.find_for_document(doc_id)
            if existing_id is not None:
                conversation = await self.get(existing_id)
                return conversation, False

            conversation_id = ids.new_id("conv")
            await self._rows.create(conversation_id, doc_id)
            loaded = await self._rows.load(conversation_id)
            conversation = _from_row(loaded or {"conversation_id": conversation_id, "doc_id": doc_id})
            self._live[conversation_id] = conversation
            log.info("started conversation %s for document %s", conversation_id, doc_id)
            return conversation, True

    async def get(self, conversation_id: str) -> Conversation:
        live = self._live.get(conversation_id)
        if live is not None:
            return live
        loaded = await self._rows.load(conversation_id)
        if loaded is None:
            raise ConversationNotFound(
                f"conversation {conversation_id!r} does not exist. It was never started, or "
                "it belongs to a different database."
            )
        conversation = _from_row(loaded)
        self._live[conversation_id] = conversation
        return conversation

    def forget(self, conversation_id: str) -> None:
        """Drop the in-memory copy. Tests, and the only way to prove a reload is real."""
        self._live.pop(conversation_id, None)

    # ------------------------------------------------------------------ appending

    async def append_message(
        self,
        conversation: Conversation,
        role: MessageRole,
        content: str = "",
        *,
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
        message_id: str | None = None,
    ) -> Message:
        message = Message(
            message_id=message_id or ids.new_id("msg"),
            conversation_id=conversation.conversation_id,
            seq=conversation.next_message_seq(),
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            created_at=utcnow().isoformat(),
        )
        conversation.messages.append(message)
        await self._rows.append_message(message)
        await self._rows.touch(conversation.conversation_id, conversation.status)
        return message

    async def append_event(
        self, conversation: Conversation, event: str, payload: dict[str, Any]
    ) -> RecordedEvent:
        """Persist an event and fan it out to live subscribers, in that order.

        Persist first: a subscriber that receives an event which is then lost to a write
        failure has been told something the reloaded transcript will not contain, and the
        two views of the conversation disagree from that point on.
        """
        record = RecordedEvent(seq=conversation.next_event_seq(), event=event, payload=payload)
        await self._rows.append_event(conversation.conversation_id, record)
        await conversation.fan_out(record)
        return record

    async def set_status(self, conversation: Conversation, status: str) -> None:
        conversation.status = status
        await self._rows.touch(conversation.conversation_id, status)


def _from_row(payload: dict) -> Conversation:
    conversation = Conversation(
        conversation_id=payload["conversation_id"],
        doc_id=payload["doc_id"],
        status=payload.get("status", "active"),
        created_at=payload.get("created_at", ""),
        updated_at=payload.get("updated_at", ""),
    )
    conversation.messages = list(payload.get("messages", []))
    conversation.events = list(payload.get("events", []))
    return conversation


def InMemoryConversationStore() -> ConversationStore:  # noqa: N802 - factory named for its result
    """A `ConversationStore` over in-memory rows."""
    return ConversationStore(InMemoryConversationRowStore())


def PostgresConversationStore(session_scope: Any) -> ConversationStore:  # noqa: N802
    """A `ConversationStore` over the three `chat_*` tables."""
    return ConversationStore(PostgresConversationRowStore(session_scope))
