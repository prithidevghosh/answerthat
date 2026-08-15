"""The command loop's retry discipline, and the approval gate on versioning."""

from __future__ import annotations

import pytest
from conftest import AlwaysRenders
from fakes import (
    BagOfWordsEmbedder,
    InMemoryDocumentStore,
    ScriptedClaims,
    ScriptedPlanner,
    ScriptedRetrieval,
    ScriptedTextModel,
    ScriptedVerifier,
)

from app.agent.executor import OperationExecutor
from app.agent.kernel import InvariantKernel
from app.agent.loop import MAX_RETRIES, CommandLoop
from app.agent.metrics import MetricsRegistry
from app.agent.planner import Planner
from app.agent.versioning import ApprovalError, ApprovalRequest, OrphanDecision, VersionService
from app.core.contracts import KernelRejection, VerificationLabel

SHORTEN_PLAN = {
    "operations": [{"op": "Shorten", "target_ids": ["blk-1"], "params": {"ratio": 0.6}}]
}
BAD_TARGET_PLAN = {
    "operations": [{"op": "Shorten", "target_ids": ["blk-nonexistent"], "params": {"ratio": 0.6}}]
}
FABRICATING_PLAN = {
    "operations": [
        {
            "op": "ReplaceCitation",
            "target_ids": ["span-1"],
            "params": {"anchor_id": "anc-1", "new_source_id": "s2:fabricated"},
        }
    ]
}
UNGATED_FREEFORM_PLAN = {
    "operations": [
        {"op": "FreeformEdit", "target_ids": ["blk-1"], "params": {"instruction": "make it snappier"}}
    ]
}

CLEAN_REWRITE = (
    "Transformers dominate sequence modelling. Attention scales quadratically with length."
)
DESTRUCTIVE_REWRITE = "Entirely unrelated content about protein folding in yeast."


def build_loop(sources, planner_responses, *, text_output=CLEAN_REWRITE, claims=None,
               candidates=None, metrics=None):
    kernel = InvariantKernel(sources, AlwaysRenders())
    executor = OperationExecutor(
        sources=sources,
        retrieval=ScriptedRetrieval(default=candidates or []),
        verifier=ScriptedVerifier(default=VerificationLabel.SUPPORTS),
        claims=ScriptedClaims(claims or []),
        embedder=BagOfWordsEmbedder(),
        text_model=ScriptedTextModel(text_output),
    )
    model = ScriptedPlanner(planner_responses)
    loop = CommandLoop(
        planner=Planner(model),
        executor=executor,
        kernel=kernel,
        metrics=metrics or MetricsRegistry(),
    )
    return loop, model, kernel


# ===========================================================================
# The happy path
# ===========================================================================


@pytest.mark.asyncio
async def test_a_valid_command_proposes_changes_without_committing(sources, base_document):
    loop, _model, _kernel = build_loop(sources, [SHORTEN_PLAN])
    result = await loop.run(base_document, "shorten the introduction")

    assert result.status == "awaiting_approval"
    assert result.attempts == 1
    assert len(result.changes) == 1
    assert result.rejected == []
    assert "until you approve" in result.message


@pytest.mark.asyncio
async def test_the_diff_shows_every_citation_persisting(sources, base_document):
    loop, _model, _kernel = build_loop(sources, [SHORTEN_PLAN])
    result = await loop.run(base_document, "shorten the introduction")

    ledger = result.changes[0].diff.citations
    assert ledger.preserved
    assert ledger.total_before == ledger.total_after == 2
    assert {a.anchor_id for a in ledger.anchors} == {"anc-1", "anc-2"}
    assert all(a.status in {"unchanged", "moved"} for a in ledger.anchors)
    assert ledger.headline == "All 2 citations preserved."


# ===========================================================================
# The retry discipline
# ===========================================================================


@pytest.mark.asyncio
async def test_a_rejection_is_handed_back_to_the_planner_with_its_reason(sources, base_document):
    loop, model, _kernel = build_loop(sources, [FABRICATING_PLAN, SHORTEN_PLAN])
    result = await loop.run(base_document, "swap that citation")

    assert result.status == "awaiting_approval"
    assert result.attempts == 2
    assert len(model.prompts) == 2
    assert "not in the source store" in model.prompts[1]
    assert "rejected by the invariant kernel" in model.prompts[1]


@pytest.mark.asyncio
async def test_an_invalid_target_is_caught_before_any_model_work(sources, base_document):
    loop, model, _kernel = build_loop(sources, [BAD_TARGET_PLAN, SHORTEN_PLAN])
    result = await loop.run(base_document, "shorten it")

    assert result.status == "awaiting_approval"
    assert "not ids in this document" in model.prompts[1]


@pytest.mark.asyncio
async def test_retries_stop_at_two_and_the_reason_survives(sources, base_document):
    loop, model, _kernel = build_loop(sources, [FABRICATING_PLAN] * 3)
    result = await loop.run(base_document, "swap that citation")

    assert result.attempts == MAX_RETRIES + 1 == 3
    assert len(model.prompts) == 3, "never retried silently forever"
    assert result.status == "failed"
    assert result.changes == []
    assert result.rejected
    assert any("not in the source store" in reason for reason in result.rejected[0].reasons)
    assert "nothing from them was applied" in result.message


@pytest.mark.asyncio
async def test_nothing_unvalidated_is_ever_applied(sources, base_document):
    """Every change that survives the loop carries an accept or flag verdict — never a
    reject, and never no verdict at all."""
    loop, _model, _kernel = build_loop(sources, [SHORTEN_PLAN])
    result = await loop.run(base_document, "shorten the introduction")
    assert all(change.verdict.decision in {"accept", "flag"} for change in result.changes)


@pytest.mark.asyncio
async def test_an_ungated_freeform_plan_is_refused_and_explained(sources, base_document):
    loop, model, _kernel = build_loop(sources, [UNGATED_FREEFORM_PLAN] * 3)
    result = await loop.run(base_document, "make it snappier")

    assert result.status == "failed"
    assert any("no_typed_op_applies" in reason for reason in result.rejected[0].reasons)
    assert "no_typed_op_applies" in model.prompts[1]


# ===========================================================================
# The FreeformEdit tripwire (ADR-009)
# ===========================================================================


@pytest.mark.asyncio
async def test_the_freeform_firing_rate_is_recorded(sources, base_document):
    metrics = MetricsRegistry()
    freeform = {
        "operations": [
            {
                "op": "FreeformEdit",
                "target_ids": ["blk-1"],
                "params": {"instruction": "reframe around efficiency"},
                "no_typed_op_applies": True,
                "justification": "the command spans two sections and no typed op covers it",
            }
        ]
    }
    loop, _model, _kernel = build_loop(sources, [freeform], metrics=metrics)
    await loop.run(base_document, "reframe my contribution")

    snapshot = metrics.snapshot()
    assert snapshot.commands == 1
    assert snapshot.freeform_operations == 1
    assert snapshot.freeform_command_rate == 1.0
    assert snapshot.justifications == ["the command spans two sections and no typed op covers it"]


def test_the_tripwire_trips_above_twenty_percent():
    from app.core.contracts import EditPlan, Operation, OperationType

    metrics = MetricsRegistry()
    typed = EditPlan(
        plan_id="p",
        operations=[Operation(op=OperationType.SHORTEN, target_ids=["b"], params={"ratio": 0.5})],
    )
    hatch = EditPlan(
        plan_id="p",
        operations=[
            Operation(
                op=OperationType.FREEFORM_EDIT,
                target_ids=["b"],
                params={"instruction": "x"},
                no_typed_op_applies=True,
                justification="because",
            )
        ],
    )
    for _ in range(4):
        metrics.record_plan(typed)
    metrics.record_plan(hatch)
    metrics.record_plan(hatch)

    snapshot = metrics.snapshot()
    assert snapshot.freeform_command_rate > 0.20
    assert snapshot.tripped


# ===========================================================================
# Approval and versioning
# ===========================================================================


async def propose(sources, base_document, plan=SHORTEN_PLAN, text_output=CLEAN_REWRITE):
    loop, _model, kernel = build_loop(sources, [plan], text_output=text_output)
    documents = InMemoryDocumentStore()
    documents.seed(base_document)
    result = await loop.run(base_document, "a command")
    return result, VersionService(documents, kernel), documents


@pytest.mark.asyncio
async def test_approving_nothing_writes_no_version(sources, base_document):
    result, versions, documents = await propose(sources, base_document)
    commit = await versions.commit(result, ApprovalRequest(change_set_id=result.change_set_id))

    assert not commit.committed
    assert await documents.list_versions("doc-1") == [1]


@pytest.mark.asyncio
async def test_approving_a_change_commits_a_new_revertible_version(sources, base_document):
    result, versions, documents = await propose(sources, base_document)
    commit = await versions.commit(
        result,
        ApprovalRequest(
            change_set_id=result.change_set_id,
            approved_change_ids=[c.change_id for c in result.changes],
        ),
    )

    assert commit.committed
    assert commit.new_version == 2
    assert await documents.list_versions("doc-1") == [1, 2]
    assert commit.diff.citations.preserved

    reverted = await versions.revert("doc-1", 1)
    assert reverted.committed and reverted.new_version == 3
    restored = await documents.get("doc-1")
    original = await documents.get("doc-1", 1)
    assert [s.text for _x, _y, s in _spans(restored)] == [s.text for _x, _y, s in _spans(original)]


@pytest.mark.asyncio
async def test_an_unresolved_orphan_blocks_the_commit(sources, base_document):
    result, versions, _documents = await propose(
        sources, base_document, text_output=DESTRUCTIVE_REWRITE
    )
    assert result.changes[0].orphans

    with pytest.raises(ApprovalError, match="waiting on your decision"):
        await versions.commit(
            result,
            ApprovalRequest(
                change_set_id=result.change_set_id,
                approved_change_ids=[c.change_id for c in result.changes],
            ),
        )


@pytest.mark.asyncio
async def test_keeping_an_orphan_puts_the_citation_back_in_the_document(sources, base_document):
    result, versions, documents = await propose(
        sources, base_document, text_output=DESTRUCTIVE_REWRITE
    )
    decisions = [
        OrphanDecision(anchor_id=orphan.anchor_id, action="keep")
        for orphan in result.changes[0].orphans
    ]
    commit = await versions.commit(
        result,
        ApprovalRequest(
            change_set_id=result.change_set_id,
            approved_change_ids=[c.change_id for c in result.changes],
            orphan_decisions=decisions,
        ),
    )

    assert commit.committed
    final = await documents.get("doc-1")
    anchors = {a.anchor_id for _x, _y, s in _spans(final) for a in s.citation_anchors}
    assert {"anc-1", "anc-2"} <= anchors
    assert commit.diff.citations.preserved


@pytest.mark.asyncio
async def test_removing_an_orphan_is_allowed_only_because_the_user_said_so(sources, base_document):
    result, versions, documents = await propose(
        sources, base_document, text_output=DESTRUCTIVE_REWRITE
    )
    decisions = [
        OrphanDecision(anchor_id=orphan.anchor_id, action="remove")
        for orphan in result.changes[0].orphans
    ]
    commit = await versions.commit(
        result,
        ApprovalRequest(
            change_set_id=result.change_set_id,
            approved_change_ids=[c.change_id for c in result.changes],
            orphan_decisions=decisions,
        ),
    )

    assert commit.committed
    final = await documents.get("doc-1")
    anchors = {a.anchor_id for _x, _y, s in _spans(final) for a in s.citation_anchors}
    assert anchors == set()
    # And the loss is stated in the diff rather than hidden.
    assert not commit.diff.citations.preserved
    assert commit.diff.citations.sources_lost == {"s2:aaa": 1, "s2:bbb": 1}


@pytest.mark.asyncio
async def test_a_decision_naming_an_anchor_that_is_not_waiting_is_refused(sources, base_document):
    result, versions, _documents = await propose(sources, base_document)
    with pytest.raises(ApprovalError, match="not waiting on a decision"):
        await versions.commit(
            result,
            ApprovalRequest(
                change_set_id=result.change_set_id,
                approved_change_ids=[c.change_id for c in result.changes],
                orphan_decisions=[OrphanDecision(anchor_id="anc-1", action="remove")],
            ),
        )


@pytest.mark.asyncio
async def test_moving_an_orphan_requires_a_destination(sources, base_document):
    result, versions, _documents = await propose(
        sources, base_document, text_output=DESTRUCTIVE_REWRITE
    )
    with pytest.raises(ApprovalError, match="needs a target_span_id"):
        await versions.commit(
            result,
            ApprovalRequest(
                change_set_id=result.change_set_id,
                approved_change_ids=[c.change_id for c in result.changes],
                orphan_decisions=[
                    OrphanDecision(anchor_id=o.anchor_id, action="move")
                    for o in result.changes[0].orphans
                ],
            ),
        )


@pytest.mark.asyncio
async def test_the_commit_boundary_refuses_an_unapproved_loss(sources, base_document, monkeypatch):
    """Belt and braces: even if a change slipped through per-change checking, the
    composed result is checked against the base version before anything is written."""
    result, versions, documents = await propose(sources, base_document)
    approved = result.changes[0]

    # Forge a change that strips an anchor, and neutralise the per-change kernel so only
    # the commit-boundary check stands between it and the store.
    stripped = base_document.model_copy(deep=True)
    stripped.sections[0].blocks[0].spans[0].citation_anchors = []
    approved.change.new_fragment = {
        "replace_spans": [stripped.sections[0].blocks[0].spans[0].model_dump()]
    }
    monkeypatch.setattr(
        versions._kernel,
        "evaluate",
        lambda **kwargs: __import__("app.core.contracts", fromlist=["KernelVerdict"]).KernelVerdict(
            decision="accept", reasons=[], flags=[]
        ),
    )

    with pytest.raises(KernelRejection, match="without an approved removal"):
        await versions.commit(
            result,
            ApprovalRequest(
                change_set_id=result.change_set_id, approved_change_ids=[approved.change_id]
            ),
        )
    assert await documents.list_versions("doc-1") == [1]


@pytest.mark.asyncio
async def test_approving_an_unknown_change_id_is_an_error(sources, base_document):
    result, versions, _documents = await propose(sources, base_document)
    with pytest.raises(ApprovalError, match="not in this set"):
        await versions.commit(
            result,
            ApprovalRequest(change_set_id=result.change_set_id, approved_change_ids=["nope"]),
        )


# ===========================================================================
# B2's source-store warming contract (memory.md §5, B2 → B3)
# ===========================================================================


@pytest.mark.asyncio
async def test_the_loop_warms_every_source_id_before_the_kernel_checks_it(base_document):
    """An unwarmed id raises instead of reporting absence, so a missed warm would surface
    as a crash — or worse, be caught somewhere and read as a fabricated source."""
    from conftest import FakeSourceReader

    strict = FakeSourceReader(["s2:aaa", "s2:bbb", "openalex:W123"], strict=True)
    loop, _model, _kernel = build_loop(
        strict,
        [{
            "operations": [{
                "op": "ReplaceCitation",
                "target_ids": ["span-1"],
                "params": {"anchor_id": "anc-1", "new_source_id": "openalex:W123"},
            }]
        }],
    )
    result = await loop.run(base_document, "swap that citation")

    assert result.status == "awaiting_approval", result.rejected
    # The swap leaves only the incoming id in the change, and that is precisely the set
    # REJECT rule 1 looks up — so it is precisely the set that must be warmed.
    assert "openalex:W123" in strict.warmed


@pytest.mark.asyncio
async def test_a_fabricated_id_is_warmed_too_so_the_reject_rests_on_a_real_answer(base_document):
    """The fabricated id must be warmed as well: it comes back known-absent, and REJECT
    rule 1 then fires on an answer rather than on an exception."""
    from conftest import FakeSourceReader

    strict = FakeSourceReader(["s2:aaa", "s2:bbb"], strict=True)
    loop, _model, _kernel = build_loop(strict, [FABRICATING_PLAN] * 3)
    result = await loop.run(base_document, "swap that citation")

    assert result.status == "failed"
    assert "s2:fabricated" in strict.warmed
    assert any("not in the source store" in r for r in result.rejected[0].reasons)


@pytest.mark.asyncio
async def test_commit_warms_before_re_judging(sources, base_document):
    """The commit path reaches the kernel too, and re-judges against the current document."""
    from conftest import FakeSourceReader

    strict = FakeSourceReader(["s2:aaa", "s2:bbb", "openalex:W123"], strict=True)
    loop, _model, kernel = build_loop(strict, [SHORTEN_PLAN])
    documents = InMemoryDocumentStore()
    documents.seed(base_document)
    result = await loop.run(base_document, "shorten it")
    strict.warmed.clear()

    versions = VersionService(documents, kernel)
    commit = await versions.commit(
        result,
        ApprovalRequest(
            change_set_id=result.change_set_id,
            approved_change_ids=[c.change_id for c in result.changes],
        ),
    )
    assert commit.committed
    assert {"s2:aaa", "s2:bbb"} <= strict.warmed


def _spans(document):
    from app.agent.fragment import iter_spans

    return list(iter_spans(document))
