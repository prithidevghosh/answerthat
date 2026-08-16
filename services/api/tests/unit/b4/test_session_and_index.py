"""Persistence, stream replay, and the evidence index.

The replay test is the one worth reading twice. `ReviewJob.subscribe` snapshots its log
and attaches its queue under one lock, because doing either first loses or duplicates the
events in between; `Conversation` copies that design and this asserts it still holds.
Nothing about a transcript is acceptable if reloading it changes what it says.
"""

from __future__ import annotations

import asyncio

import pytest
from b4_fakes import FakeEmbedder, ScriptedModel, say, settle

from app.orchestrator.index import EvidenceIndex, InMemoryEvidenceRowStore
from app.orchestrator.runtime import Orchestrator
from app.orchestrator.session import (
    ConversationNotFound,
    ConversationStore,
    InMemoryConversationRowStore,
)

# --------------------------------------------------------------------------- persistence


async def test_a_conversation_reloads_intact_after_the_process_forgets_it() -> None:
    """The point of ADR-032: a restart must not cost the record of what was said.

    The in-memory copy is dropped and the store is asked again — which is what a fresh
    process does, and the only way to prove the rows carry the conversation rather than
    the objects that happen to still be around.
    """
    rows = InMemoryConversationRowStore()
    store = ConversationStore(rows)
    conversation, created = await store.start("doc-1")
    assert created is True

    await store.append_message(conversation, "user", "how many references?")
    await store.append_message(
        conversation,
        "assistant",
        "",
        tool_calls=[{"id": "c1", "type": "function", "function": {"name": "get_parse_report", "arguments": "{}"}}],
    )
    await store.append_message(conversation, "tool", "47 references detected.", tool_call_id="c1")
    await store.append_message(conversation, "assistant", "Forty-seven, of which 39 resolved.")
    await store.append_event(conversation, "done", {"tokens_used": 120})

    # The process forgets everything it was holding.
    store.forget(conversation.conversation_id)
    reloaded = await ConversationStore(rows).get(conversation.conversation_id)

    assert reloaded.doc_id == "doc-1"
    assert [m.role for m in reloaded.messages] == ["user", "assistant", "tool", "assistant"]
    assert reloaded.messages[0].content == "how many references?"
    assert reloaded.messages[2].tool_call_id == "c1"
    assert reloaded.messages[-1].content == "Forty-seven, of which 39 resolved."
    assert [e.event for e in reloaded.events] == ["done"]
    # And the model's view survives too: a reloaded conversation must be sendable back to
    # `converse()`, tool call and matching result included, or the first turn after a
    # restart is a malformed request.
    openai = reloaded.openai_messages()
    assert openai[1]["tool_calls"][0]["id"] == "c1"
    assert openai[2] == {"role": "tool", "tool_call_id": "c1", "content": "47 references detected."}


async def test_one_conversation_per_document() -> None:
    """A second conversation about the same paper would split the record in half, and
    neither half would be the transcript."""
    store = ConversationStore(InMemoryConversationRowStore())
    first, created_first = await store.start("doc-1")
    second, created_second = await store.start("doc-1")

    assert (created_first, created_second) == (True, False)
    assert first.conversation_id == second.conversation_id


async def test_an_unknown_conversation_raises_rather_than_returning_an_empty_one() -> None:
    """An empty transcript renders as "you have not said anything yet", which is a
    different and much more confusing claim than "this does not exist"."""
    store = ConversationStore(InMemoryConversationRowStore())
    with pytest.raises(ConversationNotFound, match="does not exist"):
        await store.get("conv_never_created")


async def test_a_system_notice_reaches_the_model_as_a_user_turn_not_a_system_one() -> None:
    """A second system message mid-conversation competes with the standing prompt for
    authority. A notice is information arriving from outside, which is what a user turn is."""
    store = ConversationStore(InMemoryConversationRowStore())
    conversation, _ = await store.start("doc-1")
    await store.append_message(conversation, "system_notice", "Parsing finished. 47 references.")

    message = conversation.openai_messages()[0]
    assert message["role"] == "user"
    assert message["content"].startswith("[system notice]")


# --------------------------------------------------------------------------- replay


async def test_subscribing_mid_turn_yields_the_backlog_then_live_events(
    conversations, context, settings
) -> None:
    """The `ReviewJob.subscribe` property, one level up.

    A browser that refreshes halfway through a review must repaint everything already said
    and then follow live — with nothing dropped and nothing shown twice.
    """
    model = ScriptedModel([say("Parsing is at the arbiter stage.")])
    orchestrator = Orchestrator(
        model=model, conversations=conversations, tool_context=context, settings=settings
    )
    conversation, _ = await conversations.start("doc-1")

    await orchestrator.send_user_message(conversation, "how is it going?")
    await settle()
    already_said = [e.seq for e in conversation.events]
    assert already_said, "the test needs a backlog to be meaningful"

    backlog, queue = await conversation.subscribe()
    assert [e.seq for e in backlog] == already_said

    await conversations.append_event(conversation, "progress", {"kind": "parse", "fraction": 0.75})
    live = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert live.seq == already_said[-1] + 1
    assert live.payload == {"kind": "parse", "fraction": 0.75}
    # Sequence numbers are contiguous across the join, which is what "nothing dropped and
    # nothing duplicated" means concretely.
    assert [e.seq for e in backlog] + [live.seq] == list(range(len(already_said) + 1))


async def test_two_subscribers_see_the_same_events() -> None:
    store = ConversationStore(InMemoryConversationRowStore())
    conversation, _ = await store.start("doc-1")

    _b1, first = await conversation.subscribe()
    _b2, second = await conversation.subscribe()
    await store.append_event(conversation, "message_delta", {"text": "hello"})

    assert (await asyncio.wait_for(first.get(), 1.0)).payload == {"text": "hello"}
    assert (await asyncio.wait_for(second.get(), 1.0)).payload == {"text": "hello"}


async def test_events_are_persisted_before_they_are_fanned_out() -> None:
    """A subscriber told something the reloaded transcript will not contain leaves the two
    views of the conversation disagreeing from that point on."""
    rows = InMemoryConversationRowStore()
    store = ConversationStore(rows)
    conversation, _ = await store.start("doc-1")
    await store.append_event(conversation, "done", {"tokens_used": 5})

    persisted = (await rows.load(conversation.conversation_id))["events"]
    assert [e.event for e in persisted] == ["done"]


# --------------------------------------------------------------------------- index


async def test_the_index_reports_which_kinds_it_holds() -> None:
    """"The index is still building, these results are partial" is a real answer.

    Silently returning fewer hits is not: three results and a thin index look exactly like
    three results and a paper that does not discuss the topic.
    """
    index = EvidenceIndex(
        rows=InMemoryEvidenceRowStore(),
        embedder=FakeEmbedder(),
        model="text-embedding-3-small",
        dimensions=4,
        batch_size=8,
        text_chars=2000,
    )
    await index.build(
        "doc-1",
        [("span", "span-1", "Transformers dominate sequence modelling."),
         ("span", "span-2", "Attention scales quadratically with length.")],
    )

    hits, status = await index.search("doc-1", "attention scaling", k=5)

    assert status["kinds_indexed"] == ["span"]
    assert sorted(status["kinds_missing"]) == ["abstract", "claim", "finding"]
    assert status["rows"] == 2
    assert {hit.ref_id for hit in hits} == {"span-1", "span-2"}
    assert all(hit.kind == "span" for hit in hits)


async def test_rebuilding_does_not_re_embed_what_is_already_indexed() -> None:
    """The index is additive and review output arrives in waves. Re-embedding the paper's
    spans on every wave would multiply the bill by the number of waves."""
    embedder = FakeEmbedder()
    index = EvidenceIndex(
        rows=InMemoryEvidenceRowStore(),
        embedder=embedder,
        model="text-embedding-3-small",
        dimensions=4,
        batch_size=8,
        text_chars=2000,
    )
    await index.build("doc-1", [("span", "span-1", "First sentence.")])
    calls_after_first = len(embedder.calls)

    written = await index.build(
        "doc-1", [("span", "span-1", "First sentence."), ("claim", "clm-1", "A claim.")]
    )

    assert written == 1, "only the new row should have been embedded"
    assert len(embedder.calls) == calls_after_first + 1
    assert index.status("doc-1").kinds_indexed == ["claim", "span"]


async def test_search_is_scoped_to_one_document() -> None:
    """Cosine over the document's own rows. Another paper's spans are not candidates."""
    index = EvidenceIndex(
        rows=InMemoryEvidenceRowStore(),
        embedder=FakeEmbedder(),
        model="text-embedding-3-small",
        dimensions=4,
        batch_size=8,
        text_chars=2000,
    )
    await index.build("doc-1", [("span", "mine", "My paper's sentence.")])
    await index.build("doc-2", [("span", "theirs", "Someone else's sentence.")])

    hits, _status = await index.search("doc-1", "sentence", k=10)

    assert [hit.ref_id for hit in hits] == ["mine"]


async def test_kinds_filter_narrows_the_search(context) -> None:
    index = EvidenceIndex(
        rows=InMemoryEvidenceRowStore(),
        embedder=FakeEmbedder(),
        model="text-embedding-3-small",
        dimensions=4,
        batch_size=8,
        text_chars=2000,
    )
    await index.build(
        "doc-1",
        [("span", "span-1", "A sentence in the paper."), ("abstract", "s2:aaa", "An abstract.")],
    )

    hits, _status = await index.search("doc-1", "paper", k=10, kinds=["abstract"])

    assert [hit.kind for hit in hits] == ["abstract"]


async def test_an_empty_index_returns_nothing_and_says_so_rather_than_erroring() -> None:
    index = EvidenceIndex(
        rows=InMemoryEvidenceRowStore(),
        embedder=FakeEmbedder(),
        model="text-embedding-3-small",
        dimensions=4,
        batch_size=8,
        text_chars=2000,
    )
    hits, status = await index.search("doc-nothing", "anything", k=5)

    assert hits == []
    assert status["state"] == "empty"
    assert status["rows"] == 0
