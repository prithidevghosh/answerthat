"""The confirmation gate. ADR-033.

There is no separate approval screen in the agentic flow — a chat confirmation is enough
to commit — so every guarantee the edit console makes visually rests on this gate. These
tests assert on `FakeVersions.commits` rather than on what the agent said, because what
matters is whether a version was *written*, not whether the model claimed to have asked.
"""

from __future__ import annotations

from b4_fakes import ScriptedModel, call, multi, say, settle

from app.core.llm import ToolCall
from app.orchestrator.runtime import Orchestrator

CHANGE_SET = {
    "change_set_id": "cs-123",
    "doc_id": "doc-1",
    "base_version": 1,
    "command": "shorten the introduction",
    "status": "awaiting_approval",
    "attempts": 1,
    "changes": [
        {
            "change": {"change_id": "chg-1"},
            "verdict": {"decision": "accept", "reasons": []},
            "orphans": [],
        }
    ],
    "rejected": [],
}


def _orchestrator(model, conversations, context, settings) -> Orchestrator:
    return Orchestrator(
        model=model, conversations=conversations, tool_context=context, settings=settings
    )


async def test_commit_in_the_same_turn_as_the_proposal_is_refused(
    conversations, context, settings, commands, versions
) -> None:
    """The exact jailbreak the gate exists for: propose and commit in one breath.

    A prompt-level rule is satisfied by a model that writes "Shall I commit? Yes,
    committing." The gate does not read the prose — it asks whether a *user message*
    arrived after the proposal was shown.
    """
    commands.change_set = CHANGE_SET
    model = ScriptedModel(
        [
            multi(
                ToolCall(call_id="c1", name="propose_edit",
                         arguments={"doc_id": "doc-1", "instruction": "shorten the introduction"}),
                ToolCall(call_id="c2", name="commit_change_set",
                         arguments={"change_set_id": "cs-123", "approved_change_ids": ["chg-1"],
                                    "rejected_change_ids": None, "orphan_decisions": None}),
            ),
            say("I have proposed the change. Shall I commit it?"),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")

    await orchestrator.send_user_message(conversation, "shorten the introduction")
    await settle()

    assert versions.commits == [], "nothing may be written in the turn that proposed it"
    refusals = [
        event.payload
        for event in conversation.events
        if event.event == "tool_result" and event.payload["name"] == "commit_change_set"
    ]
    assert refusals and refusals[0]["ok"] is False
    assert "cannot run in the same turn" in refusals[0]["error"]
    assert refusals[0]["data"]["kind"] == "confirmation_required"


async def test_the_same_commit_succeeds_after_a_user_message(
    conversations, context, settings, commands, versions
) -> None:
    """The other half: the gate blocks an unconfirmed commit, it does not block commits."""
    commands.change_set = CHANGE_SET
    model = ScriptedModel(
        [
            call("propose_edit", {"doc_id": "doc-1", "instruction": "shorten the introduction"}),
            say("Here is the change. It touches one span. Shall I commit it?"),
            call(
                "commit_change_set",
                {
                    "change_set_id": "cs-123",
                    "approved_change_ids": ["chg-1"],
                    "rejected_change_ids": None,
                    "orphan_decisions": None,
                },
            ),
            say("Committed as version 2."),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")

    await orchestrator.send_user_message(conversation, "shorten the introduction")
    await settle()
    assert versions.commits == []

    await orchestrator.send_user_message(conversation, "yes, commit it")
    await settle()

    assert len(versions.commits) == 1
    assert versions.commits[0]["approved_change_ids"] == ["chg-1"]


async def test_the_committed_base_version_is_the_proposal_s_not_the_model_s(
    conversations, context, settings, commands, versions
) -> None:
    """ADR-021's lock is pinned from the stored change set.

    The model does not get to restate `base_version`. If it could, the optimistic lock
    would be a number the agent could talk its way around, and a commit against a moved
    head would land on whatever the head had become.
    """
    commands.change_set = {**CHANGE_SET, "base_version": 4}
    model = ScriptedModel(
        [
            call("propose_edit", {"doc_id": "doc-1", "instruction": "tighten section 2"}),
            say("Proposed. Shall I commit?"),
            call(
                "commit_change_set",
                {
                    "change_set_id": "cs-123",
                    "approved_change_ids": ["chg-1"],
                    "rejected_change_ids": None,
                    "orphan_decisions": None,
                },
            ),
            say("Done."),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")
    await orchestrator.send_user_message(conversation, "tighten section 2")
    await settle()
    await orchestrator.send_user_message(conversation, "yes")
    await settle()

    assert versions.commits[0]["base_version"] == 4


async def test_a_proposal_emits_the_structured_change_set_for_the_ui(
    conversations, context, settings, commands
) -> None:
    """`awaiting_confirmation` carries the real diff, not the agent's summary of it.

    The user approves the proposal, and the proposal is the structured object. A UI that
    rendered the prose would be asking them to approve a paraphrase.
    """
    commands.change_set = CHANGE_SET
    model = ScriptedModel(
        [
            call("propose_edit", {"doc_id": "doc-1", "instruction": "shorten it"}),
            say("Here is what I would change."),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")
    await orchestrator.send_user_message(conversation, "shorten it")
    await settle()

    pending = [e.payload for e in conversation.events if e.event == "awaiting_confirmation"]
    assert len(pending) == 1
    assert pending[0]["kind"] == "change_set"
    assert pending[0]["proposal"]["change_set_id"] == "cs-123"


async def test_export_is_gated_too(conversations, context, settings, exporter) -> None:
    """`export_latex` hands the user a file, so it is confirmable for the same reason."""
    model = ScriptedModel(
        [
            multi(
                ToolCall(call_id="m1", name="get_export_manifest", arguments={"doc_id": "doc-1", "version": None}),
                ToolCall(call_id="m2", name="export_latex", arguments={"doc_id": "doc-1", "version": None}),
            ),
            say("Here is the manifest. Shall I render it?"),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")
    await orchestrator.send_user_message(conversation, "export it")
    await settle()

    assert exporter.rendered == [], "no file may be produced in the turn that disclosed it"
    refusal = next(
        e.payload
        for e in conversation.events
        if e.event == "tool_result" and e.payload["name"] == "export_latex"
    )
    assert refusal["ok"] is False


async def test_a_failed_change_set_is_not_a_proposal(
    conversations, context, settings, commands, versions
) -> None:
    """A change set the kernel rejected outright cannot be confirmed into existence.

    Without this, "propose (fails) → user says anything → commit" would satisfy the gate
    on a proposal that never described a valid edit.
    """
    commands.change_set = {
        **CHANGE_SET,
        "status": "failed",
        "changes": [],
        "rejected": [{"reasons": ["would drop 1× s2:aaa without an approved removal"], "attempt": 2}],
    }
    model = ScriptedModel(
        [
            call("propose_edit", {"doc_id": "doc-1", "instruction": "delete the citations"}),
            say("The kernel refused: it would drop a citation."),
            call(
                "commit_change_set",
                {
                    "change_set_id": "cs-123",
                    "approved_change_ids": [],
                    "rejected_change_ids": None,
                    "orphan_decisions": None,
                },
            ),
            say("I could not commit that."),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")
    await orchestrator.send_user_message(conversation, "delete the citations")
    await settle()
    assert not [e for e in conversation.events if e.event == "awaiting_confirmation"]

    await orchestrator.send_user_message(conversation, "go ahead")
    await settle()
    assert versions.commits == []
