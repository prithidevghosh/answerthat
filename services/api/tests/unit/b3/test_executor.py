"""The seven operations, end to end through the kernel.

Each test asserts the operation does its job *and* that the kernel accepts (or flags) the
result — because an operation that produces something the kernel rejects is not
implemented, it is merely written.
"""

from __future__ import annotations

import pytest
from b3_fakes import (
    BagOfWordsEmbedder,
    FakeFingerprintStore,
    ScriptedClaims,
    ScriptedRetrieval,
    ScriptedTextModel,
    ScriptedVerifier,
)
from b3_support import TEST_BAND, AlwaysRenders

from app.agent.executor import ExecutionError, OperationExecutor, resolve_blocks
from app.agent.kernel import (
    FLAG_ORPHANED_ANCHOR,
    FLAG_WEAK_VERIFICATION,
    REJECT_UNKNOWN_SOURCE_ID,
    InvariantKernel,
)
from app.agent.operations import OperationSpecError, parse_params
from app.core.contracts import Claim, Operation, OperationType, VerificationLabel


def build_executor(sources, *, text_output="rewritten prose here.", claims=None,
                   candidates=None, labels=None, default_label=VerificationLabel.SUPPORTS):
    return OperationExecutor(
        sources=sources,
        retrieval=ScriptedRetrieval(default=candidates or []),
        verifier=ScriptedVerifier(labels or {}, default=default_label),
        claims=ScriptedClaims(claims or []),
        embedder=BagOfWordsEmbedder(),
        text_model=ScriptedTextModel(text_output),
        fingerprints=FakeFingerprintStore(),
        band=TEST_BAND,
    )


def op(kind: OperationType, targets: list[str], **params) -> Operation:
    extra = {}
    if kind is OperationType.FREEFORM_EDIT:
        extra = {"no_typed_op_applies": True, "justification": "no typed operation covers this"}
    return Operation(op=kind, target_ids=targets, params=params, **extra)


# ===========================================================================
# Target resolution
# ===========================================================================


def test_resolve_blocks_expands_a_section_to_its_blocks(base_document):
    assert [b.id for b in resolve_blocks(base_document, ["sec-1"])] == ["blk-1"]


def test_resolve_blocks_accepts_a_span_id(base_document):
    assert [b.id for b in resolve_blocks(base_document, ["span-3"])] == ["blk-2"]


def test_an_invented_target_id_is_an_error_not_an_empty_result(base_document):
    with pytest.raises(ExecutionError, match="do not exist"):
        resolve_blocks(base_document, ["sec-imaginary"])


# ===========================================================================
# AddCitations / FindSupport
# ===========================================================================


@pytest.mark.asyncio
async def test_add_citations_inserts_a_verified_anchor(sources, base_document, kernel):
    claims = [Claim(claim_id="c1", text="We train on a single GPU.", span_id="span-3", citability=0.9)]
    executor = build_executor(sources, claims=claims, candidates=["openalex:W123"])

    execution = await executor.execute(base_document, op(OperationType.ADD_CITATIONS, ["sec-2"], count=1))

    assert execution.ok, execution.error
    assert execution.change.new_source_ids == ["openalex:W123"]
    verdict = kernel.evaluate(
        before=base_document, change=execution.change, context=execution.context
    )
    assert verdict.decision == "accept", verdict.reasons

    after = kernel.project(base_document, execution.change)
    added = after.sections[1].blocks[0].spans[0].citation_anchors
    assert [a.source_ids for a in added] == [["openalex:W123"]]
    assert added[0].provenance_kind == "agent_added"


@pytest.mark.asyncio
async def test_add_citations_does_not_change_a_single_character_of_prose(sources, base_document, kernel):
    claims = [Claim(claim_id="c1", text="We train on a single GPU.", span_id="span-3", citability=0.9)]
    executor = build_executor(sources, claims=claims, candidates=["openalex:W123"])
    execution = await executor.execute(base_document, op(OperationType.ADD_CITATIONS, ["sec-2"], count=1))

    after = kernel.project(base_document, execution.change)
    assert after.sections[1].blocks[0].spans[0].text == "We train on a single GPU."


@pytest.mark.asyncio
async def test_a_claim_whose_candidates_all_fail_verification_stays_uncited_and_says_so(
    sources, base_document
):
    claims = [Claim(claim_id="c1", text="We train on a single GPU.", span_id="span-3", citability=0.9)]
    executor = build_executor(
        sources,
        claims=claims,
        candidates=["openalex:W123"],
        default_label=VerificationLabel.DOES_NOT_ADDRESS,
    )
    execution = await executor.execute(base_document, op(OperationType.ADD_CITATIONS, ["sec-2"], count=1))

    assert not execution.ok
    assert "none verified" in execution.error


@pytest.mark.asyncio
async def test_zero_candidates_is_reported_never_hidden(sources, base_document):
    """HR-3: a search that found nothing is a visible state, not an empty success."""
    claims = [Claim(claim_id="c1", text="We train on a single GPU.", span_id="span-3", citability=0.9)]
    executor = build_executor(sources, claims=claims, candidates=[])
    execution = await executor.execute(base_document, op(OperationType.ADD_CITATIONS, ["sec-2"], count=1))

    assert not execution.ok
    assert "no candidate sources found" in execution.error


@pytest.mark.asyncio
async def test_a_partially_supporting_citation_is_added_but_flagged(sources, base_document, kernel):
    claims = [Claim(claim_id="c1", text="We train on a single GPU.", span_id="span-3", citability=0.9)]
    executor = build_executor(
        sources,
        claims=claims,
        candidates=["openalex:W123"],
        default_label=VerificationLabel.PARTIALLY_SUPPORTS,
    )
    execution = await executor.execute(base_document, op(OperationType.ADD_CITATIONS, ["sec-2"], count=1))
    verdict = kernel.evaluate(before=base_document, change=execution.change, context=execution.context)

    assert verdict.decision == "flag"
    assert FLAG_WEAK_VERIFICATION in verdict.flags


@pytest.mark.asyncio
async def test_find_support_proposes_anchors_for_named_claims_only(sources, base_document):
    claims = [
        Claim(claim_id="c1", text="Transformers dominate.", span_id="span-1", citability=0.9,
              anchor_ids=["anc-1"]),
        Claim(claim_id="c2", text="We train on a single GPU.", span_id="span-3", citability=0.5),
    ]
    executor = build_executor(sources, claims=claims, candidates=["openalex:W123"])
    execution = await executor.execute(
        base_document, op(OperationType.FIND_SUPPORT, ["sec-1", "sec-2"], claim_ids=["c2"])
    )

    assert execution.ok, execution.error
    assert "span-3" in execution.change.change_id


# ===========================================================================
# Shorten / RewriteSection / FreeformEdit
# ===========================================================================


@pytest.mark.asyncio
async def test_shorten_compresses_and_keeps_every_citation(sources, base_document, kernel):
    executor = build_executor(
        sources,
        text_output=(
            "Transformers dominate sequence modelling. "
            "Attention scales quadratically with length."
        ),
    )
    execution = await executor.execute(base_document, op(OperationType.SHORTEN, ["blk-1"], ratio=0.5))

    assert execution.ok, execution.error
    verdict = kernel.evaluate(before=base_document, change=execution.change, context=execution.context)
    assert verdict.decision == "accept", verdict.reasons

    after = kernel.project(base_document, execution.change)
    anchors = {a.anchor_id for s in after.sections for b in s.blocks for sp in b.spans
               for a in sp.citation_anchors}
    assert {"anc-1", "anc-2"} <= anchors


@pytest.mark.asyncio
async def test_a_shorten_that_orphans_an_anchor_flags_rather_than_deletes(sources, base_document, kernel):
    executor = build_executor(sources, text_output="Entirely unrelated content about protein folding.")
    execution = await executor.execute(base_document, op(OperationType.SHORTEN, ["blk-1"], ratio=0.3))

    assert execution.ok, execution.error
    assert set(execution.change.orphaned_anchor_ids) == {"anc-1", "anc-2"}
    verdict = kernel.evaluate(before=base_document, change=execution.change, context=execution.context)
    assert verdict.decision == "flag"
    assert verdict.flags.count(FLAG_ORPHANED_ANCHOR) == 2


@pytest.mark.asyncio
async def test_a_text_model_returning_nothing_produces_no_change_at_all(sources, base_document):
    executor = build_executor(sources, text_output="")
    execution = await executor.execute(base_document, op(OperationType.SHORTEN, ["blk-1"], ratio=0.5))

    assert not execution.ok
    assert "nothing usable" in execution.error


@pytest.mark.asyncio
async def test_rewrite_section_requires_an_instruction(sources, base_document):
    executor = build_executor(sources)
    execution = await executor.execute(base_document, op(OperationType.REWRITE_SECTION, ["blk-1"]))
    assert not execution.ok
    assert "instruction" in execution.error


@pytest.mark.asyncio
async def test_freeform_edit_runs_the_same_transform_discipline(sources, base_document, kernel):
    executor = build_executor(
        sources,
        text_output=(
            "Transformers dominate sequence modelling. Attention scales quadratically with length."
        ),
    )
    execution = await executor.execute(
        base_document, op(OperationType.FREEFORM_EDIT, ["blk-1"], instruction="reframe for efficiency")
    )
    assert execution.ok, execution.error
    verdict = kernel.evaluate(before=base_document, change=execution.change, context=execution.context)
    assert verdict.decision == "accept", verdict.reasons
    assert any("generic invariants" in note for note in execution.notes)


def test_freeform_edit_without_the_gate_is_refused():
    bad = Operation(
        op=OperationType.FREEFORM_EDIT, target_ids=["blk-1"], params={"instruction": "do a thing"}
    )
    with pytest.raises(OperationSpecError, match="no_typed_op_applies=True"):
        parse_params(bad)


def test_freeform_edit_without_a_justification_is_refused():
    bad = Operation(
        op=OperationType.FREEFORM_EDIT,
        target_ids=["blk-1"],
        params={"instruction": "do a thing"},
        no_typed_op_applies=True,
    )
    with pytest.raises(OperationSpecError, match="justification"):
        parse_params(bad)


def test_the_gate_flag_is_meaningless_on_a_typed_operation():
    bad = Operation(
        op=OperationType.SHORTEN, target_ids=["blk-1"], params={"ratio": 0.5}, no_typed_op_applies=True
    )
    with pytest.raises(OperationSpecError, match="only meaningful on FreeformEdit"):
        parse_params(bad)


# ===========================================================================
# ReplaceCitation / MoveText
# ===========================================================================


@pytest.mark.asyncio
async def test_replace_citation_swaps_the_source_and_keeps_the_anchor(sources, base_document, kernel):
    executor = build_executor(sources)
    execution = await executor.execute(
        base_document,
        op(OperationType.REPLACE_CITATION, ["span-1"], anchor_id="anc-1", new_source_id="openalex:W123"),
    )
    assert execution.ok, execution.error
    verdict = kernel.evaluate(before=base_document, change=execution.change, context=execution.context)
    assert verdict.decision == "accept", verdict.reasons

    after = kernel.project(base_document, execution.change)
    anchor = after.sections[0].blocks[0].spans[0].citation_anchors[0]
    assert anchor.anchor_id == "anc-1"
    assert anchor.source_ids == ["openalex:W123"]
    assert anchor.offset_in_span == 41  # position preserved


@pytest.mark.asyncio
async def test_replace_citation_refuses_a_source_id_that_is_not_in_the_store(sources, base_document):
    executor = build_executor(sources)
    execution = await executor.execute(
        base_document,
        op(OperationType.REPLACE_CITATION, ["span-1"], anchor_id="anc-1", new_source_id="s2:invented"),
    )
    assert not execution.ok
    assert "not in the source store" in execution.error


@pytest.mark.asyncio
async def test_move_text_relocates_blocks_with_their_anchors(sources, base_document, kernel):
    executor = build_executor(sources)
    execution = await executor.execute(
        base_document, op(OperationType.MOVE_TEXT, ["blk-1"], to_section_id="sec-2")
    )
    assert execution.ok, execution.error
    verdict = kernel.evaluate(before=base_document, change=execution.change, context=execution.context)
    assert verdict.decision == "accept", verdict.reasons

    after = kernel.project(base_document, execution.change)
    assert [b.id for b in after.sections[0].blocks] == []
    assert [b.id for b in after.sections[1].blocks] == ["blk-1", "blk-2"]
    assert after.sections[1].blocks[0].spans[0].citation_anchors[0].anchor_id == "anc-1"


@pytest.mark.asyncio
async def test_move_text_to_a_section_that_does_not_exist_is_an_error(sources, base_document):
    executor = build_executor(sources)
    execution = await executor.execute(
        base_document, op(OperationType.MOVE_TEXT, ["blk-1"], to_section_id="sec-nowhere")
    )
    assert not execution.ok
    assert "does not exist" in execution.error


# ===========================================================================
# HR-1 at the executor boundary
# ===========================================================================


@pytest.mark.asyncio
async def test_an_executor_cannot_introduce_a_source_the_store_does_not_have(sources, base_document):
    """Retrieval is the only door. If a fake retrieval returns an id the store never saw,
    the kernel stops it — which is the guarantee we actually rely on."""
    claims = [Claim(claim_id="c1", text="We train on a single GPU.", span_id="span-3", citability=0.9)]
    executor = build_executor(sources, claims=claims, candidates=["s2:never-written"])
    execution = await executor.execute(base_document, op(OperationType.ADD_CITATIONS, ["sec-2"], count=1))

    kernel = InvariantKernel(sources, AlwaysRenders())
    verdict = kernel.evaluate(before=base_document, change=execution.change, context=execution.context)
    assert verdict.decision == "reject"
    assert REJECT_UNKNOWN_SOURCE_ID in {r.split(":", 1)[0] for r in verdict.reasons}
