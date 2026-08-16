"""What the loop does when things go wrong. HR-3, one level up from the pipeline.

Every case here has the same shape: something failed, and the question is whether the user
can *see* that it failed. A conversation that stops mid-sentence, a tool that never
returns, a budget that runs out silently — each is indistinguishable from an agent that
simply had nothing more to say.
"""

from __future__ import annotations

from b4_fakes import ScriptedModel, call, say, settle

from app.core.llm import TokenBudgetExceeded
from app.orchestrator.runtime import Orchestrator
from app.orchestrator.tools import ToolResult


def _orchestrator(model, conversations, context, settings) -> Orchestrator:
    return Orchestrator(
        model=model, conversations=conversations, tool_context=context, settings=settings
    )


async def test_a_tool_that_raises_becomes_a_turn_not_a_crash(
    conversations, context, settings, registry
) -> None:
    """A provider 429 mid-conversation must not kill the loop.

    The exception becomes `ok=False` with its message, the model reads it, and the turn
    continues to an answer. The traceback is logged separately — a one-line user-facing
    string is an honest terminal state and a useless bug report.
    """

    async def explode(**_kwargs) -> ToolResult:
        raise RuntimeError("Semantic Scholar returned 429")

    broken = registry.get("get_parse_report")
    object.__setattr__(broken, "handler", explode)

    model = ScriptedModel(
        [
            call("get_parse_report", {"doc_id": "doc-1", "include": "counts"}),
            say("I could not read the parse report: Semantic Scholar returned 429."),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    orchestrator._registries["x"] = registry  # noqa: SLF001 - inject the sabotaged registry
    conversation, _ = await conversations.start("doc-1")
    orchestrator._registries[conversation.conversation_id] = registry  # noqa: SLF001

    await orchestrator.send_user_message(conversation, "how many references?")
    await settle()

    result = next(e.payload for e in conversation.events if e.event == "tool_result")
    assert result["ok"] is False
    assert "429" in result["error"]
    # The loop kept going: an assistant message and a `done` event both landed.
    assert any(e.event == "done" for e in conversation.events)
    assert conversation.messages[-1].role == "assistant"


async def test_the_iteration_cap_terminates_with_a_visible_message(
    conversations, context, settings
) -> None:
    """A silent stop is indistinguishable from an answer."""
    settings.orchestrator_max_iterations = 3
    model = ScriptedModel(
        [call("get_parse_progress", {"doc_id": "doc-1"}) for _ in range(3)]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")

    await orchestrator.send_user_message(conversation, "keep going")
    await settle()

    errors = [e.payload for e in conversation.events if e.event == "error"]
    assert errors and errors[-1]["error"] == "iteration_cap_reached"
    assert "3 steps" in errors[-1]["detail"]
    # And as a message in the transcript, not only as an error event: the user reads the
    # transcript, and an error banner alone leaves the conversation looking unfinished.
    assert conversation.messages[-1].role == "assistant"
    assert "stopped" in conversation.messages[-1].content


async def test_token_budget_exhaustion_is_a_chat_error_and_the_conversation_survives(
    conversations, context, settings
) -> None:
    """ADR-015: nothing is truncated to fit, so the user is told the budget is spent.

    The failure mode this rules out is a conversation that quietly answers with less — the
    same false negative as a review that silently dropped half a paper's claims.
    """
    model = ScriptedModel([say("never reached")])
    model.raises = TokenBudgetExceeded(
        "document 'doc-1' would use 2,000,100 LLM tokens, over its budget of 2,000,000."
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")

    await orchestrator.send_user_message(conversation, "summarise the findings")
    await settle()

    errors = [e.payload for e in conversation.events if e.event == "error"]
    assert errors and errors[-1]["error"] == "token_budget_exceeded"
    assert "over its budget" in errors[-1]["detail"]
    # Still readable: the user's message is in the log and the conversation is loadable.
    assert conversation.messages[0].role == "user"
    reloaded = await conversations.get(conversation.conversation_id)
    assert reloaded.messages[0].content == "summarise the findings"


async def test_an_unknown_tool_name_is_a_refusal_that_lists_the_real_ones(
    conversations, context, settings
) -> None:
    """A hallucinated tool name is the model's mistake, and it can fix it if told."""
    model = ScriptedModel(
        [
            call("summarise_everything", {"doc_id": "doc-1"}),
            say("That is not something I can do; here is what I can."),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")
    await orchestrator.send_user_message(conversation, "summarise everything")
    await settle()

    result = next(e.payload for e in conversation.events if e.event == "tool_result")
    assert result["ok"] is False
    assert "no tool named" in result["error"]
    assert "get_parse_report" in result["error"]


async def test_bad_arguments_come_back_as_a_tool_result(conversations, context, settings) -> None:
    """The model can retry a call it got wrong, but only if it is told what was wrong."""
    model = ScriptedModel(
        [
            call("get_span", {"doc_id": "doc-1", "span_id": "span-1", "colour": "blue"}),
            say("Let me try that again."),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")
    await orchestrator.send_user_message(conversation, "read span-1")
    await settle()

    result = next(e.payload for e in conversation.events if e.event == "tool_result")
    assert result["ok"] is False
    assert "could not be called with those arguments" in result["error"]


async def test_deltas_stream_before_the_complete_message(conversations, context, settings) -> None:
    """The user must see the agent typing, not one clump at the end."""
    model = ScriptedModel([say("Forty-seven references were detected.")])
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")
    await orchestrator.send_user_message(conversation, "how many references?")
    await settle()

    names = [e.event for e in conversation.events]
    assert names.index("message_start") < names.index("message_delta")
    assert names.index("message_delta") < len(names) - 1
    assert names[-1] == "done"


async def test_a_tool_call_is_announced_with_its_human_label(
    conversations, context, settings
) -> None:
    """The UI's chrome comes from the registry, never from something the agent wrote."""
    model = ScriptedModel(
        [call("get_parse_report", {"doc_id": "doc-1", "include": "counts"}), say("47 references.")]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")
    await orchestrator.send_user_message(conversation, "how many references?")
    await settle()

    announced = next(e.payload for e in conversation.events if e.event == "tool_call")
    assert announced["label"] == "Reading the parse report"
    assert announced["name"] == "get_parse_report"
