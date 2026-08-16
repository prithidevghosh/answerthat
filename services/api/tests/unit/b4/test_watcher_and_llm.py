"""The watcher's progress/transition split, and `converse()`'s streaming assembly.

Two distinct concerns in one file because both are about the same thing arriving in
pieces: a background job's state over time, and a model's answer over the wire.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from b4_fakes import FakeIndex, ScriptedModel, say, settle

from app.core.contracts import LLMRole
from app.core.llm import (
    LLMRecordingMissing,
    OpenAILLMClient,
    StructuredOutputError,
    TokenBudget,
    TokenBudgetExceeded,
    conversation_key,
)
from app.orchestrator.runtime import Orchestrator
from app.orchestrator.watcher import ConversationWatcher

# --------------------------------------------------------------------------- watcher


def _watcher(orchestrator, ingest, review, documents, sources, index, settings):
    return ConversationWatcher(
        orchestrator=orchestrator,
        ingest=ingest,
        review=review,
        documents=documents,
        sources=sources,
        index=index,
        settings=settings,
    )


async def test_a_finished_parse_produces_a_notice_and_exactly_one_agent_turn(
    conversations, context, settings, ingest, documents, sources, review
) -> None:
    """The notice is data; the message is the model's.

    There is no branch anywhere that composes "Parsing is done, want to see it?". The
    runtime states the counts, runs a turn, and the model writes the sentence.
    """
    model = ScriptedModel([say("Parsing finished — 47 references, 39 of them resolved.")])
    orchestrator = Orchestrator(
        model=model, conversations=conversations, tool_context=context, settings=settings
    )
    conversation, _ = await conversations.start("doc-1")
    watcher = _watcher(orchestrator, ingest, review, documents, sources, FakeIndex(), settings)

    watcher.watch_parse(conversation)
    await settle()

    notices = [m for m in conversation.messages if m.role == "system_notice"]
    assert len(notices) == 1
    # Facts, and nothing that tells the model what to say.
    assert "47 reference(s) detected" in notices[0].content
    assert "39 resolved" in notices[0].content
    for instruction in ("tell the user", "ask whether", "summarise", "offer"):
        assert instruction not in notices[0].content.lower()

    assert len(model.calls) == 1, "a state transition runs one turn, not one per poll"
    assert conversation.messages[-1].content.startswith("Parsing finished")


async def test_progress_ticks_are_ui_events_and_never_run_a_turn(
    conversations, context, settings, ingest, documents, sources, review
) -> None:
    """Running a turn per tick would spend the document's token budget narrating a
    progress bar, and fill the chat with "still going!"."""
    ingest._status = {  # noqa: SLF001
        "state": "running",
        "stage": "arbiter",
        "progress": 0.625,
        "version": None,
        "elapsed_s": 30.0,
        "error": None,
    }
    model = ScriptedModel([])
    orchestrator = Orchestrator(
        model=model, conversations=conversations, tool_context=context, settings=settings
    )
    conversation, _ = await conversations.start("doc-1")
    watcher = _watcher(orchestrator, ingest, review, documents, sources, FakeIndex(), settings)

    watcher.watch_parse(conversation)
    await settle()
    watcher.stop(conversation.conversation_id)

    progress = [e.payload for e in conversation.events if e.event == "progress"]
    assert progress, "the stage change must reach the UI"
    assert progress[0] == {
        "kind": "parse",
        "state": "running",
        "stage": "arbiter",
        "fraction": 0.625,
        "elapsed_s": 30.0,
    }
    assert model.calls == [], "no agent turn may run for a progress tick"


async def test_a_failed_parse_reports_the_pipeline_s_own_reason(
    conversations, context, settings, ingest, documents, sources, review
) -> None:
    ingest._status = {  # noqa: SLF001
        "state": "failed",
        "stage": "grobid",
        "progress": 0.125,
        "version": None,
        "elapsed_s": 4.0,
        "error": "GrobidUnavailable: connection refused after 180s",
    }
    model = ScriptedModel([say("Parsing failed: GROBID could not be reached.")])
    orchestrator = Orchestrator(
        model=model, conversations=conversations, tool_context=context, settings=settings
    )
    conversation, _ = await conversations.start("doc-1")
    watcher = _watcher(orchestrator, ingest, review, documents, sources, FakeIndex(), settings)

    watcher.watch_parse(conversation)
    await settle()

    notice = next(m for m in conversation.messages if m.role == "system_notice")
    assert "connection refused after 180s" in notice.content
    assert "GrobidUnavailable" in notice.content


async def test_review_findings_stream_as_progress_and_completion_runs_a_turn(
    conversations, context, settings, ingest, documents, sources, review
) -> None:
    # The watcher waits for a job to exist before subscribing, so the fake has to report
    # one. That wait is the fix for a real defect: the watcher is started when the
    # conversation opens, long before the user has agreed to a review, and subscribing
    # immediately died on the runner's "no review was started" refusal.
    review._status = {"status": "running", "job_id": "rev_1"}  # noqa: SLF001
    review.events = [
        ("progress", {"phase": "claims_extracted", "verified": 0, "total": 2}),
        # `kind` here is the real shape of a `Finding` payload, and it is the whole point
        # of this fixture: it is the key that collided with the envelope's discriminator.
        ("finding", {"finding_id": "fnd_1", "kind": "missing_work", "severity": "high",
                     "claim": {"claim_id": "clm_1", "text": "A claim."}}),
        ("complete", {"verified": 2, "total": 2, "findings": 1, "candidates_considered": 9,
                      "quote_check_failures": 4, "unverifiable_no_abstract": 1,
                      "claims_without_candidates": 0}),
    ]
    model = ScriptedModel([say("The review finished with one finding.")])
    orchestrator = Orchestrator(
        model=model, conversations=conversations, tool_context=context, settings=settings
    )
    conversation, _ = await conversations.start("doc-1")
    watcher = _watcher(orchestrator, ingest, review, documents, sources, FakeIndex(), settings)

    watcher.watch_review(conversation)
    await settle()

    forwarded = [e.payload for e in conversation.events if e.event == "progress"]
    assert any(p.get("event") == "finding" for p in forwarded)
    # Every one carries the `parse`/`review` discriminator the frontend switches on. This
    # is the assertion that caught the real bug: a `Finding` has its own `kind` field, and
    # splatting the payload over the envelope replaced "review" with "missing_work", so
    # every finding event was invisible to a client filtering for review progress.
    assert all(p["kind"] == "review" for p in forwarded)
    finding = next(p for p in forwarded if p["event"] == "finding")
    assert finding["finding"]["finding_id"] == "fnd_1"
    assert finding["finding"]["kind"] == "missing_work", "the finding keeps its own kind, nested"

    notice = next(m for m in conversation.messages if m.role == "system_notice")
    # The secondary counters travel with the notice: a one-finding review that killed four
    # candidates on the quote check is a different report from one that found nothing to kill.
    assert "4 were discarded" in notice.content
    assert "1 had no retrievable abstract" in notice.content
    assert len(model.calls) == 1


async def test_the_watcher_waits_for_a_review_that_does_not_exist_yet(
    conversations, context, settings, ingest, documents, sources, review
) -> None:
    """The ordering defect, pinned.

    A review watcher is started when the conversation opens. At that moment there is
    almost never a review — the user has to be told the plan and agree first, which takes
    at least two turns. Subscribing immediately hit the runner's deliberate refusal for a
    document whose review was never started, the watcher died, and every finding the
    review later produced went unforwarded while the counters climbed. Found by running a
    real review through the live stack; the watcher now waits for the job to appear.
    """
    review._status = {"status": "not_started"}  # noqa: SLF001
    review.events = [("complete", {"verified": 1, "total": 1, "findings": 0})]
    model = ScriptedModel([say("The review finished.")])
    orchestrator = Orchestrator(
        model=model, conversations=conversations, tool_context=context, settings=settings
    )
    conversation, _ = await conversations.start("doc-1")
    watcher = _watcher(orchestrator, ingest, review, documents, sources, FakeIndex(), settings)

    watcher.watch_review(conversation)
    await settle()
    assert model.calls == [], "nothing to report until a review exists"

    # The review starts, three turns later, exactly as it does in the product.
    review._status = {"status": "running", "job_id": "rev_1"}  # noqa: SLF001
    # A real sleep, not just event-loop yields: the watcher's wait is a timed poll, so
    # letting it notice requires the clock to move past one interval.
    await asyncio.sleep(settings.orchestrator_watch_interval_s * 3)
    await settle()

    assert len([m for m in conversation.messages if m.role == "system_notice"]) == 1
    assert len(model.calls) == 1


async def test_a_second_watcher_for_the_same_conversation_is_a_no_op(
    conversations, context, settings, ingest, documents, sources, review
) -> None:
    """Two watchers would announce every transition twice."""
    model = ScriptedModel([say("Parsing finished.")])
    orchestrator = Orchestrator(
        model=model, conversations=conversations, tool_context=context, settings=settings
    )
    conversation, _ = await conversations.start("doc-1")
    watcher = _watcher(orchestrator, ingest, review, documents, sources, FakeIndex(), settings)

    watcher.watch_parse(conversation)
    watcher.watch_parse(conversation)
    await settle()

    assert len([m for m in conversation.messages if m.role == "system_notice"]) == 1


async def test_a_completed_parse_schedules_the_span_index(
    conversations, context, settings, ingest, documents, sources, review
) -> None:
    index = FakeIndex()
    model = ScriptedModel([say("Done.")])
    orchestrator = Orchestrator(
        model=model, conversations=conversations, tool_context=context, settings=settings
    )
    conversation, _ = await conversations.start("doc-1")
    watcher = _watcher(orchestrator, ingest, review, documents, sources, index, settings)

    watcher.watch_parse(conversation)
    await settle()

    assert index.scheduled, "the paper's spans must be indexed once they are persisted"
    doc_id, texts = index.scheduled[0]
    assert doc_id == "doc-1"
    assert {kind for kind, _ref, _text in texts} == {"span"}
    assert ("span", "span-1", "Transformers dominate sequence modelling.") in texts


# --------------------------------------------------------------------------- converse


class FakeStream:
    """Reproduces OpenAI's streaming shape, including the part that bites.

    Tool call arguments arrive **split across chunks and identified only by index**; the
    id and the name come once, on the first fragment, and the fragments that follow carry
    empty strings for both. A client that treats one chunk as one call produces a call
    whose arguments are the first eight characters of a JSON object.
    """

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for chunk in self._chunks:
                yield chunk

        return gen()


class _Obj:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def _chunk(*, content=None, tool_fragments=None, finish=None, usage=None):
    delta = _Obj(content=content, tool_calls=tool_fragments)
    return _Obj(choices=[_Obj(delta=delta, finish_reason=finish)], usage=usage)


def _fragment(index: int, *, call_id=None, name=None, arguments=None):
    return _Obj(index=index, id=call_id, function=_Obj(name=name, arguments=arguments))


class FakeSettingsForLLM:
    model_orchestrate = "gpt-5.5"
    embedding_model = "text-embedding-3-small"
    embedding_dimensions = 512
    openai_api_key = "sk-test"
    llm_timeout_s = 30.0

    def __init__(self, recordings: Path, mode: str = "live") -> None:
        self.llm_recordings_dir = recordings
        self.llm_mode = mode

    def model_for(self, role: LLMRole) -> str:  # noqa: ARG002
        return self.model_orchestrate


class FakeOpenAIForConverse:
    def __init__(self, chunks: list) -> None:
        self.chunks = chunks
        self.requests: list[dict] = []
        self.chat = _Obj(completions=self)

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return FakeStream(self.chunks)


def _client(tmp_path: Path, fake, mode: str = "live") -> OpenAILLMClient:
    return OpenAILLMClient(
        FakeSettingsForLLM(tmp_path, mode),  # type: ignore[arg-type]
        budget=TokenBudget(limit=1_000_000),
        client=fake,
    )


async def test_tool_call_fragments_are_assembled_by_index(tmp_path: Path) -> None:
    fake = FakeOpenAIForConverse(
        [
            _chunk(content="Let me "),
            _chunk(content="check."),
            _chunk(tool_fragments=[_fragment(0, call_id="call_a", name="get_parse_report", arguments='{"doc')]),
            _chunk(tool_fragments=[_fragment(0, arguments='_id": "doc-1", ')]),
            _chunk(tool_fragments=[_fragment(1, call_id="call_b", name="get_style", arguments='{"doc_id"')]),
            _chunk(tool_fragments=[_fragment(0, arguments='"include": "counts"}')]),
            _chunk(tool_fragments=[_fragment(1, arguments=': "doc-1"}')]),
            _chunk(finish="tool_calls", usage=_Obj(total_tokens=321)),
        ]
    )
    deltas: list[str] = []

    turn = await _client(tmp_path, fake).converse(
        LLMRole.ORCHESTRATE,
        [{"role": "user", "content": "how many references?"}],
        tools=[{"type": "function", "function": {"name": "get_parse_report"}}],
        on_text=lambda text: _collect(deltas, text),
    )

    assert deltas == ["Let me ", "check."], "text must arrive progressively, not in a clump"
    assert turn.text == "Let me check."
    assert len(turn.tool_calls) == 2
    assert turn.tool_calls[0].name == "get_parse_report"
    assert turn.tool_calls[0].arguments == {"doc_id": "doc-1", "include": "counts"}
    assert turn.tool_calls[1].arguments == {"doc_id": "doc-1"}
    assert turn.tokens == 321


async def _collect(sink: list[str], text: str) -> None:
    sink.append(text)


async def test_finish_reason_length_is_a_refusal(tmp_path: Path) -> None:
    """A truncated turn may cut mid tool-call arguments, producing a syntactically valid
    object with a missing field that the agent would then act on."""
    fake = FakeOpenAIForConverse(
        [_chunk(content="The refere"), _chunk(finish="length", usage=_Obj(total_tokens=9))]
    )
    with pytest.raises(StructuredOutputError, match="truncated"):
        await _client(tmp_path, fake).converse(
            LLMRole.ORCHESTRATE, [{"role": "user", "content": "hi"}], on_text=_noop
        )


async def _noop(_text: str) -> None:
    return None


async def test_malformed_tool_arguments_raise_rather_than_defaulting(tmp_path: Path) -> None:
    """An empty argument object is a legal call for several tools, so a default here would
    run the wrong operation on the user's paper and look intentional."""
    fake = FakeOpenAIForConverse(
        [
            _chunk(tool_fragments=[_fragment(0, call_id="c", name="commit_change_set", arguments="{not json")]),
            _chunk(finish="tool_calls", usage=_Obj(total_tokens=5)),
        ]
    )
    with pytest.raises(StructuredOutputError, match="not valid JSON"):
        await _client(tmp_path, fake).converse(
            LLMRole.ORCHESTRATE, [{"role": "user", "content": "commit"}], on_text=_noop
        )


async def test_the_token_budget_charges_and_raises(tmp_path: Path) -> None:
    fake = FakeOpenAIForConverse([_chunk(content="ok", finish="stop", usage=_Obj(total_tokens=500))])
    client = OpenAILLMClient(
        FakeSettingsForLLM(tmp_path),  # type: ignore[arg-type]
        budget=TokenBudget(limit=400),
        client=fake,
    )
    with pytest.raises(TokenBudgetExceeded, match="over its budget"):
        await client.converse(
            LLMRole.ORCHESTRATE, [{"role": "user", "content": "hi"}], doc_id="doc-1", on_text=_noop
        )


async def test_record_then_replay_round_trips_the_assembled_turn(tmp_path: Path) -> None:
    """The recording holds the assembled turn, never the raw chunks: chunk boundaries are
    a property of one network session, not of the answer."""
    messages = [{"role": "user", "content": "how many references?"}]
    tools = [{"type": "function", "function": {"name": "get_parse_report"}}]
    fake = FakeOpenAIForConverse(
        [
            _chunk(content="Checking."),
            _chunk(tool_fragments=[_fragment(0, call_id="c1", name="get_parse_report", arguments='{"doc_id": "doc-1"}')]),
            _chunk(finish="tool_calls", usage=_Obj(total_tokens=77)),
        ]
    )
    await _client(tmp_path, fake, mode="record").converse(
        LLMRole.ORCHESTRATE, messages, tools=tools, on_text=_noop
    )

    replayed = await _client(tmp_path, None, mode="replay").converse(
        LLMRole.ORCHESTRATE, messages, tools=tools, on_text=_noop
    )

    assert replayed.text == "Checking."
    assert replayed.tool_calls[0].arguments == {"doc_id": "doc-1"}
    assert replayed.finish_reason == "tool_calls"


async def test_replay_with_no_recording_raises_rather_than_calling_the_network(
    tmp_path: Path,
) -> None:
    with pytest.raises(LLMRecordingMissing, match="does not fall through to the network"):
        await _client(tmp_path, None, mode="replay").converse(
            LLMRole.ORCHESTRATE, [{"role": "user", "content": "unrecorded"}]
        )


def test_the_recording_key_covers_the_whole_message_list_and_the_tools() -> None:
    """Keying on the last message alone would replay one recording for two different
    conversations that happen to end the same way — and do it silently, because the
    response would still be well-formed."""
    tail = [{"role": "user", "content": "and now?"}]
    first = conversation_key(LLMRole.ORCHESTRATE, "gpt-5.5", [{"role": "user", "content": "a"}, *tail], None, None)
    second = conversation_key(LLMRole.ORCHESTRATE, "gpt-5.5", [{"role": "user", "content": "b"}, *tail], None, None)
    assert first != second

    with_tools = conversation_key(
        LLMRole.ORCHESTRATE, "gpt-5.5", tail, [{"type": "function", "function": {"name": "x"}}], None
    )
    assert with_tools != conversation_key(LLMRole.ORCHESTRATE, "gpt-5.5", tail, None, None)
