"""HR-5 and HR-1 through the chat: the two guarantees a conversation could quietly erode.

The deterministic flow enforces both with a screen — an approval page that will not
submit until every orphaned anchor has a radio button chosen, and a source store with no
`put` reachable from the agent. The chat has neither. These tests are the argument that it
does not need them.
"""

from __future__ import annotations

import pytest
from b4_fakes import ScriptedModel, call, say, settle

from app.orchestrator.runtime import Orchestrator
from app.orchestrator.tools import Toolbox

ORPHANED_CHANGE_SET = {
    "change_set_id": "cs-orphan",
    "doc_id": "doc-1",
    "base_version": 1,
    "command": "rewrite the introduction",
    "status": "awaiting_approval",
    "attempts": 1,
    "changes": [
        {
            "change": {"change_id": "chg-1"},
            "verdict": {"decision": "flag", "reasons": ["one anchor could not be reattached"]},
            "orphans": [
                {
                    "anchor_id": "anc-1",
                    "marker": "[1]",
                    "source_ids": ["s2:aaa"],
                    "best_span_id": "span-2",
                    "best_span_text": "Attention scales quadratically.",
                    "score": 0.61,
                    "threshold": 0.72,
                    "flag_floor": 0.55,
                    "actions": ["keep", "move", "remove"],
                }
            ],
        }
    ],
    "rejected": [],
}


def _orchestrator(model, conversations, context, settings) -> Orchestrator:
    return Orchestrator(
        model=model, conversations=conversations, tool_context=context, settings=settings
    )


async def test_an_undecided_orphan_blocks_the_commit_even_with_an_explicit_yes(
    conversations, context, settings, commands, versions
) -> None:
    """HR-5. A plain "yes" is the one confirmation an orphaned anchor cannot settle.

    The user said yes to the edit. They did not say what happens to a citation that no
    longer has a sentence to sit in, and there is no default — not "keep", and certainly
    not "remove". The refusal names the anchor so the agent can put the actual choice to
    them.
    """
    commands.change_set = ORPHANED_CHANGE_SET
    versions.undecided_orphans = ["anc-1"]
    model = ScriptedModel(
        [
            call("propose_edit", {"doc_id": "doc-1", "instruction": "rewrite the introduction"}),
            say("One citation could not be reattached. Shall I commit?"),
            call(
                "commit_change_set",
                {
                    "change_set_id": "cs-orphan",
                    "approved_change_ids": ["chg-1"],
                    "rejected_change_ids": None,
                    "orphan_decisions": None,
                },
            ),
            say("I cannot commit until you decide about the citation."),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")
    await orchestrator.send_user_message(conversation, "rewrite the introduction")
    await settle()
    await orchestrator.send_user_message(conversation, "yes, commit it")
    await settle()

    assert versions.commits == [], "no version may be written with an anchor undecided"
    refusal = next(
        e.payload
        for e in conversation.events
        if e.event == "tool_result" and e.payload["name"] == "commit_change_set"
    )
    assert refusal["ok"] is False
    assert "anc-1" in refusal["error"], "the refusal must name the anchor, not just count it"
    assert "keep, move, or remove" in refusal["error"]


async def test_the_commit_lands_once_each_anchor_has_a_decision(
    conversations, context, settings, commands, versions
) -> None:
    commands.change_set = ORPHANED_CHANGE_SET
    versions.undecided_orphans = ["anc-1"]
    model = ScriptedModel(
        [
            call("propose_edit", {"doc_id": "doc-1", "instruction": "rewrite the introduction"}),
            say("Citation [1] lost its home. Keep it, move it, or remove it?"),
            call(
                "commit_change_set",
                {
                    "change_set_id": "cs-orphan",
                    "approved_change_ids": ["chg-1"],
                    "rejected_change_ids": None,
                    "orphan_decisions": [
                        {"anchor_id": "anc-1", "action": "move", "target_span_id": "span-2"}
                    ],
                },
            ),
            say("Committed."),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")
    await orchestrator.send_user_message(conversation, "rewrite the introduction")
    await settle()
    await orchestrator.send_user_message(conversation, "move it to the next sentence")
    await settle()

    assert len(versions.commits) == 1
    assert versions.commits[0]["orphan_decisions"] == [
        {"anchor_id": "anc-1", "action": "move", "target_span_id": "span-2"}
    ]


async def test_the_proposal_the_ui_receives_carries_every_orphan_with_its_score(
    conversations, context, settings, commands
) -> None:
    """The user cannot decide about an anchor they cannot see.

    `awaiting_confirmation` has to carry the marker, the sentence it sat in, the best
    candidate home and the score it fell short by — the same four facts the deterministic
    orphan card shows. An agent summary saying "one citation needs attention" is not a
    decision surface.
    """
    commands.change_set = ORPHANED_CHANGE_SET
    model = ScriptedModel(
        [
            call("propose_edit", {"doc_id": "doc-1", "instruction": "rewrite it"}),
            say("Here is the change."),
        ]
    )
    orchestrator = _orchestrator(model, conversations, context, settings)
    conversation, _ = await conversations.start("doc-1")
    await orchestrator.send_user_message(conversation, "rewrite it")
    await settle()

    proposal = next(
        e.payload["proposal"] for e in conversation.events if e.event == "awaiting_confirmation"
    )
    orphan = proposal["changes"][0]["orphans"][0]
    assert orphan["marker"] == "[1]"
    assert orphan["best_span_text"] == "Attention scales quadratically."
    assert (orphan["score"], orphan["threshold"]) == (0.61, 0.72)
    assert orphan["actions"] == ["keep", "move", "remove"]


# --------------------------------------------------------------------------- HR-1


async def test_a_source_id_that_is_not_in_the_store_is_refused(context) -> None:
    """HR-1. An id the model produced from memory is not a source.

    `get_source` is the only way a source reaches the conversation, and it answers from
    the append-only store. An id that is not there was never retrieved from an HTTP
    response, so there is nothing to describe — and describing it anyway is precisely the
    fabricated citation the whole architecture exists to make impossible.
    """
    box = Toolbox(context, "doc-1")

    real = await box.get_source("s2:aaa")
    assert real.ok is True
    assert real.data["provenance"]["external_url"] == "https://example.org/s2:aaa"

    invented = await box.get_source("s2:this-id-was-never-retrieved")
    assert invented.ok is False
    assert "there is no source" in invented.error
    assert "HR-1" in invented.error


def test_no_orchestrator_path_can_write_to_the_source_store(context) -> None:
    """Structural, not procedural: the port has no `put`.

    Checked on the object the tools actually hold rather than on the Protocol, because a
    Protocol is a claim about a shape and this is a claim about the thing that was bound.
    """
    assert not hasattr(context.sources, "put")
    assert sorted(m for m in ("get", "has", "warm") if hasattr(context.sources, m)) == [
        "get",
        "has",
        "warm",
    ]


def test_the_orchestrator_package_imports_no_forbidden_package() -> None:
    """The rule that keeps the packages separable, and the first one quietly violated.

    `app/orchestrator/` declares Protocols and `app/api/deps.py` binds implementations.
    A module-level import of `app.review` here would compile, pass every other test, and
    silently undo the arrangement — so it is asserted on the source rather than trusted.

    Function-level imports are exempt and deliberately so: `tools.py` reaches for
    `ChangeSetNotFound` and `ApprovalError` inside a handler, because catching a specific
    exception type requires naming it and there is no way to express "the error another
    package raises" as a Protocol.
    """
    import ast
    from pathlib import Path

    forbidden = {"app.parsing", "app.review", "app.agent", "app.providers", "app.ir", "app.export"}
    package = Path(__file__).resolve().parents[4] / "app" / "orchestrator"
    offences: list[str] = []

    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # Only module-level imports. A `for` at column 0 inside the module body is
            # what "this package depends on that package" actually means.
            if not isinstance(node, ast.ImportFrom) or node.col_offset != 0:
                continue
            module = node.module or ""
            if any(module == f or module.startswith(f + ".") for f in forbidden):
                offences.append(f"{path.name}:{node.lineno} imports {module}")

    # `app.ir.ids` is the one exception, and it is a deliberate one: id minting is a pure
    # string function with no domain behaviour, it is what every other package uses, and
    # a second id scheme would make orchestrator ids visibly foreign in the same database.
    offences = [o for o in offences if "app.ir.ids" not in o]
    assert offences == [], "app/orchestrator/ must depend on other packages through ports"


@pytest.mark.parametrize("tool_name", ["get_source", "search_evidence", "get_finding"])
def test_every_source_facing_tool_is_read_only(registry, tool_name: str) -> None:
    tool = registry.get(tool_name)
    assert tool is not None
    assert tool.mutating is False
    assert tool.confirm is False
