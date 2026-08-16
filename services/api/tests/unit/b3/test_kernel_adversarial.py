"""Adversarial tests for the invariant kernel.

These are the three attacks the design exists to stop (goal.md CP-8, HR-1, HR-5):
a fabricated `source_id`, a dropped anchor, an unsupported new claim. Each must REJECT.

Written before the planner existed, deliberately: the kernel is pure code and needs no
model to be tested, which is the whole argument of ADR-007.
"""

from __future__ import annotations

import pytest
from conftest import (
    AlwaysRenders,
    NeverRenders,
    does_not_address,
    make_anchor,
    make_span,
    partially_supports,
    supports,
)

from app.agent.kernel import (
    FLAG_LOW_CONFIDENCE_REATTACHMENT,
    FLAG_ORPHANED_ANCHOR,
    FLAG_WEAK_VERIFICATION,
    REJECT_CITATION_MULTISET_SHRANK,
    REJECT_IR_SCHEMA_VIOLATION,
    REJECT_PANDOC_REFUSED,
    REJECT_UNGROUNDED_NEW_CLAIM,
    REJECT_UNKNOWN_SOURCE_ID,
    ChangeContext,
    InvariantKernel,
    ReattachmentRecord,
)
from app.core.contracts import Operation, OperationType, ProposedChange


def change(fragment: dict, **kwargs) -> ProposedChange:
    return ProposedChange(
        change_id=kwargs.pop("change_id", "chg-1"),
        op=kwargs.pop(
            "op",
            Operation(op=OperationType.ADD_CITATIONS, target_ids=["sec-1"], params={}),
        ),
        new_fragment=fragment,
        new_source_ids=kwargs.pop("new_source_ids", []),
        orphaned_anchor_ids=kwargs.pop("orphaned_anchor_ids", []),
        rationale=kwargs.pop("rationale", "test"),
    )


def codes(verdict) -> set[str]:
    return {reason.split(":", 1)[0] for reason in verdict.reasons}


# ===========================================================================
# ATTACK 1 — a fabricated source_id (HR-1)
# ===========================================================================


def test_rejects_fabricated_source_id_in_an_anchor(kernel, base_document):
    """The model invents a plausible-looking id and hangs an anchor off it."""
    poisoned = make_span(
        "span-3",
        "We train on a single GPU.",
        [make_anchor("anc-new", ["s2:deadbeefdeadbeef"], offset=24)],
    )
    verdict = kernel.evaluate(
        before=base_document,
        change=change({"replace_spans": [poisoned.model_dump()]}),
        context=ChangeContext(verifications={"anc-new": supports()}),
    )

    assert verdict.decision == "reject"
    assert REJECT_UNKNOWN_SOURCE_ID in codes(verdict)
    assert verdict.reasons  # HR-3: never empty
    assert "s2:deadbeefdeadbeef" in verdict.reasons[0]


def test_rejects_fabricated_source_id_declared_only_in_new_source_ids(kernel, base_document):
    verdict = kernel.evaluate(
        before=base_document,
        change=change({}, new_source_ids=["openalex:W999999"]),
    )
    assert verdict.decision == "reject"
    assert REJECT_UNKNOWN_SOURCE_ID in codes(verdict)


def test_accepts_a_source_id_a_provider_adapter_really_wrote(kernel, base_document):
    """The only legal way to add a citation: reference an id already in the store."""
    grounded = make_span(
        "span-3",
        "We train on a single GPU.",
        [make_anchor("anc-new", ["openalex:W123"], offset=24)],
    )
    verdict = kernel.evaluate(
        before=base_document,
        change=change(
            {"replace_spans": [grounded.model_dump()]},
            new_source_ids=["openalex:W123"],
        ),
        context=ChangeContext(verifications={"anc-new": supports()}),
    )
    assert verdict.decision == "accept", verdict.reasons


def test_kernel_cannot_write_to_the_source_store(sources):
    """HR-1 structurally: the reader the kernel holds has no put()."""
    assert not hasattr(sources, "put")
    kernel = InvariantKernel(sources, AlwaysRenders())
    assert not hasattr(kernel._sources, "put")


# ===========================================================================
# ATTACK 2 — a dropped anchor (HR-5)
# ===========================================================================


def test_rejects_a_shortened_paragraph_that_quietly_loses_a_citation(kernel, base_document):
    """The classic failure: rewrite a paragraph, `[12]` doesn't come back."""
    shortened = make_span("span-1", "Transformers dominate sequence modelling.")  # anchor gone
    verdict = kernel.evaluate(
        before=base_document,
        change=change({"replace_spans": [shortened.model_dump()]}),
        context=ChangeContext(derived_spans={"span-1": ["span-1"]}),
    )

    assert verdict.decision == "reject"
    assert REJECT_CITATION_MULTISET_SHRANK in codes(verdict)
    assert "s2:aaa" in " ".join(verdict.reasons)


def test_rejects_deleting_a_block_that_carries_anchors(kernel, base_document):
    verdict = kernel.evaluate(
        before=base_document,
        change=change({"delete_block_ids": ["blk-1"]}),
    )
    assert verdict.decision == "reject"
    assert REJECT_CITATION_MULTISET_SHRANK in codes(verdict)


def test_rejects_an_anchor_that_found_no_home_and_was_not_surfaced(kernel, base_document):
    """An anchor below threshold must become a user decision — never a deletion."""
    rewritten = make_span("span-2", "Attention cost grows with the square of the length.")
    verdict = kernel.evaluate(
        before=base_document,
        change=change({"replace_spans": [rewritten.model_dump()]}),
        context=ChangeContext(
            derived_spans={"span-2": ["span-2"]},
            reattachments=[ReattachmentRecord(anchor_id="anc-2", landed_span_id=None, score=0.31, threshold=0.72)],
        ),
    )
    assert verdict.decision == "reject"
    assert REJECT_CITATION_MULTISET_SHRANK in codes(verdict)
    assert "never dropped" in " ".join(verdict.reasons)


def test_surfacing_the_same_anchor_turns_the_reject_into_a_flag(kernel, base_document):
    """Same edit, but the anchor is held for a user decision. Valid, and warned about."""
    rewritten = make_span("span-2", "Attention cost grows with the square of the length.")
    verdict = kernel.evaluate(
        before=base_document,
        change=change(
            {"replace_spans": [rewritten.model_dump()]},
            orphaned_anchor_ids=["anc-2"],
        ),
        context=ChangeContext(
            derived_spans={"span-2": ["span-2"]},
            reattachments=[ReattachmentRecord(anchor_id="anc-2", landed_span_id=None, score=0.31, threshold=0.72)],
        ),
    )
    assert verdict.decision == "flag"
    assert FLAG_ORPHANED_ANCHOR in verdict.flags
    assert verdict.reasons
    assert "your decision" in " ".join(verdict.reasons)


def test_removal_is_allowed_only_with_explicit_approval(kernel, base_document):
    stripped = make_span("span-1", "Transformers dominate sequence modelling.")
    fragment = {"replace_spans": [stripped.model_dump()]}

    without_approval = kernel.evaluate(
        before=base_document,
        change=change(fragment),
        context=ChangeContext(derived_spans={"span-1": ["span-1"]}),
    )
    assert without_approval.decision == "reject"

    with_approval = kernel.evaluate(
        before=base_document,
        change=change(fragment),
        context=ChangeContext(
            derived_spans={"span-1": ["span-1"]},
            approved_removals={"s2:aaa": 1},
        ),
    )
    assert with_approval.decision == "accept", with_approval.reasons


def test_partial_approval_does_not_cover_a_larger_loss(kernel, base_document):
    """Approving one removal must not licence two."""
    doc = base_document.model_copy(deep=True)
    doc.sections[0].blocks[0].spans[1].citation_anchors[0].source_ids = ["s2:aaa"]

    verdict = kernel.evaluate(
        before=doc,
        change=change({"delete_block_ids": ["blk-1"]}),
        context=ChangeContext(approved_removals={"s2:aaa": 1}),
    )
    assert verdict.decision == "reject"
    assert "loses 2 occurrence(s)" in " ".join(verdict.reasons)


def test_moving_text_with_its_anchors_preserves_the_multiset(kernel, base_document):
    verdict = kernel.evaluate(
        before=base_document,
        change=change(
            {"move_blocks": [{"block_id": "blk-1", "to_section_id": "sec-2", "after_block_id": "blk-2"}]},
            op=Operation(op=OperationType.MOVE_TEXT, target_ids=["blk-1"], params={"to": "sec-2"}),
        ),
    )
    assert verdict.decision == "accept", verdict.reasons


# ===========================================================================
# ATTACK 3 — an unsupported new claim
# ===========================================================================


def test_rejects_a_new_paragraph_asserted_with_no_verified_anchor(kernel, base_document):
    invented = {
        "insert_blocks": [
            {
                "section_id": "sec-2",
                "after_block_id": "blk-2",
                "block": {
                    "id": "blk-new",
                    "type": "paragraph",
                    "order": 1,
                    "spans": [make_span("span-new", "Our method outperforms all prior work.").model_dump()],
                },
            }
        ]
    }
    verdict = kernel.evaluate(before=base_document, change=change(invented))

    assert verdict.decision == "reject"
    assert REJECT_UNGROUNDED_NEW_CLAIM in codes(verdict)
    assert "span-new" in " ".join(verdict.reasons)


def test_rejects_a_new_claim_whose_anchor_does_not_support_it(kernel, base_document):
    """Cited, but the verifier said the source does not address the claim."""
    invented = {
        "insert_blocks": [
            {
                "section_id": "sec-2",
                "after_block_id": "blk-2",
                "block": {
                    "id": "blk-new",
                    "type": "paragraph",
                    "order": 1,
                    "spans": [
                        make_span(
                            "span-new",
                            "Our method outperforms all prior work.",
                            [make_anchor("anc-new", ["s2:aaa"], offset=37)],
                        ).model_dump()
                    ],
                },
            }
        ]
    }
    verdict = kernel.evaluate(
        before=base_document,
        change=change(invented),
        context=ChangeContext(verifications={"anc-new": does_not_address()}),
    )
    assert verdict.decision == "reject"
    assert REJECT_UNGROUNDED_NEW_CLAIM in codes(verdict)


def test_accepts_a_new_claim_grounded_in_a_partially_supporting_source(kernel, base_document):
    invented = {
        "insert_blocks": [
            {
                "section_id": "sec-2",
                "after_block_id": "blk-2",
                "block": {
                    "id": "blk-new",
                    "type": "paragraph",
                    "order": 1,
                    "spans": [
                        make_span(
                            "span-new",
                            "Sparse attention reduces the cost.",
                            [make_anchor("anc-new", ["s2:bbb"], offset=33)],
                        ).model_dump()
                    ],
                },
            }
        ]
    }
    verdict = kernel.evaluate(
        before=base_document,
        change=change(invented),
        context=ChangeContext(verifications={"anc-new": partially_supports()}),
    )
    # Valid — but weaker than SUPPORTS, so the user is told.
    assert verdict.decision == "flag"
    assert FLAG_WEAK_VERIFICATION in verdict.flags


def test_rejects_a_fabricated_derivation(kernel, base_document):
    """An executor cannot dodge the grounding rule by inventing a provenance chain."""
    invented = {
        "insert_blocks": [
            {
                "section_id": "sec-2",
                "after_block_id": "blk-2",
                "block": {
                    "id": "blk-new",
                    "type": "paragraph",
                    "order": 1,
                    "spans": [make_span("span-new", "Our method outperforms all prior work.").model_dump()],
                },
            }
        ]
    }
    verdict = kernel.evaluate(
        before=base_document,
        change=change(invented),
        context=ChangeContext(derived_spans={"span-new": ["span-does-not-exist"]}),
    )
    assert verdict.decision == "reject"
    assert REJECT_UNGROUNDED_NEW_CLAIM in codes(verdict)
    assert "none of which exist" in " ".join(verdict.reasons)


def test_rewriting_existing_text_is_not_a_new_assertion(kernel, base_document):
    rewritten = make_span(
        "span-3",
        "Training runs on one GPU.",
        [],
    )
    verdict = kernel.evaluate(
        before=base_document,
        change=change({"replace_spans": [rewritten.model_dump()]}),
        context=ChangeContext(derived_spans={"span-3": ["span-3"]}),
    )
    assert verdict.decision == "accept", verdict.reasons


# ===========================================================================
# REJECT rules 4 and 5
# ===========================================================================


def test_rejects_an_ir_schema_violation(kernel, base_document):
    verdict = kernel.evaluate(
        before=base_document,
        change=change({"replace_spans": [{"id": "span-1", "text": 42}]}),
    )
    assert verdict.decision == "reject"
    assert REJECT_IR_SCHEMA_VIOLATION in codes(verdict)


def test_rejects_a_fragment_pointing_at_a_node_that_does_not_exist(kernel, base_document):
    verdict = kernel.evaluate(
        before=base_document,
        change=change({"replace_spans": [make_span("span-ghost", "x").model_dump()]}),
    )
    assert verdict.decision == "reject"
    assert REJECT_IR_SCHEMA_VIOLATION in codes(verdict)


def test_rejects_an_anchor_whose_offset_falls_outside_its_span(kernel, base_document):
    bad = make_span("span-3", "Short.", [make_anchor("anc-x", ["s2:aaa"], offset=500)])
    verdict = kernel.evaluate(
        before=base_document,
        change=change({"replace_spans": [bad.model_dump()]}),
        context=ChangeContext(derived_spans={"span-3": ["span-3"]}),
    )
    assert verdict.decision == "reject"
    assert REJECT_IR_SCHEMA_VIOLATION in codes(verdict)


def test_rejects_an_orphaned_anchor_id_that_does_not_exist(kernel, base_document):
    verdict = kernel.evaluate(
        before=base_document,
        change=change({}, orphaned_anchor_ids=["anc-imaginary"]),
    )
    assert verdict.decision == "reject"
    assert REJECT_IR_SCHEMA_VIOLATION in codes(verdict)


def test_rejects_when_pandoc_refuses_to_render(sources, base_document):
    kernel = InvariantKernel(sources, NeverRenders("unbalanced \\begin{itemize}"))
    verdict = kernel.evaluate(before=base_document, change=change({}))
    assert verdict.decision == "reject"
    assert REJECT_PANDOC_REFUSED in codes(verdict)
    assert "unbalanced" in " ".join(verdict.reasons)


# ===========================================================================
# FLAG rules
# ===========================================================================


def test_flags_a_low_confidence_reattachment(kernel, base_document):
    rewritten = make_span(
        "span-2",
        "The cost of attention grows quadratically.",
        [make_anchor("anc-2", ["s2:bbb"], offset=41)],
    )
    verdict = kernel.evaluate(
        before=base_document,
        change=change({"replace_spans": [rewritten.model_dump()]}),
        context=ChangeContext(
            derived_spans={"span-2": ["span-2"]},
            reattachments=[
                ReattachmentRecord(anchor_id="anc-2", landed_span_id="span-2", score=0.61, threshold=0.82)
            ],
        ),
    )
    assert verdict.decision == "flag"
    assert FLAG_LOW_CONFIDENCE_REATTACHMENT in verdict.flags
    assert "0.610" in " ".join(verdict.reasons)


def test_a_confident_reattachment_is_not_flagged(kernel, base_document):
    rewritten = make_span(
        "span-2",
        "The cost of attention grows quadratically.",
        [make_anchor("anc-2", ["s2:bbb"], offset=41)],
    )
    verdict = kernel.evaluate(
        before=base_document,
        change=change({"replace_spans": [rewritten.model_dump()]}),
        context=ChangeContext(
            derived_spans={"span-2": ["span-2"]},
            reattachments=[
                ReattachmentRecord(anchor_id="anc-2", landed_span_id="span-2", score=0.94, threshold=0.82)
            ],
        ),
    )
    assert verdict.decision == "accept", verdict.reasons


def test_flags_an_existing_anchor_whose_source_only_partially_supports_it(kernel, base_document):
    verdict = kernel.evaluate(
        before=base_document,
        change=change({}),
        context=ChangeContext(verifications={"anc-1": partially_supports()}),
    )
    assert verdict.decision == "flag"
    assert FLAG_WEAK_VERIFICATION in verdict.flags


# ===========================================================================
# Invariants of the verdict itself
# ===========================================================================


@pytest.mark.parametrize(
    "case",
    [
        {"new_source_ids": ["s2:fake"]},
        {"fragment": {"delete_block_ids": ["blk-1"]}},
        {"fragment": {"replace_spans": [{"id": "span-1", "text": 1}]}},
    ],
)
def test_every_reject_carries_at_least_one_reason(kernel, base_document, case):
    verdict = kernel.evaluate(
        before=base_document,
        change=change(case.get("fragment", {}), new_source_ids=case.get("new_source_ids", [])),
    )
    assert verdict.decision == "reject"
    assert verdict.reasons, "HR-3: a rejection without a reason is a silent discard"


def test_accept_carries_no_reasons_and_no_flags(kernel, base_document):
    verdict = kernel.evaluate(before=base_document, change=change({}))
    assert verdict.decision == "accept"
    assert verdict.reasons == []
    assert verdict.flags == []


def test_kernel_is_pure_and_leaves_the_input_document_untouched(kernel, base_document):
    snapshot = base_document.model_dump_json()
    kernel.evaluate(
        before=base_document,
        change=change({"delete_block_ids": ["blk-2"]}),
    )
    assert base_document.model_dump_json() == snapshot


def test_kernel_module_makes_no_model_call():
    """ADR-007: the kernel is pure code, and this is checked against the syntax tree rather
    than the file's text so that prose in a docstring can neither fake a violation nor hide
    one."""
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[3] / "app/agent/kernel.py"
    tree = ast.parse(path.read_text())

    awaits = [n for n in ast.walk(tree) if isinstance(n, (ast.Await, ast.AsyncFunctionDef))]
    assert not awaits, "kernel.py is synchronous: nothing in it may await, so nothing can be I/O"

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            if node.module == "app.agent.ports":
                names = {alias.name for alias in node.names}
                assert names == {"RenderProbe", "SourceReader"}, (
                    f"the kernel's only collaborators are RenderProbe and SourceReader; found {names}"
                )

    for forbidden in ("anthropic", "openai", "httpx", "requests", "voyageai", "aiohttp"):
        assert forbidden not in imported, f"kernel.py must not import {forbidden!r}"


def test_reject_beats_flag(kernel, base_document):
    """A change that is both uncertain and invalid is rejected, not flagged."""
    stripped = make_span("span-1", "Transformers dominate sequence modelling.")
    verdict = kernel.evaluate(
        before=base_document,
        change=change({"replace_spans": [stripped.model_dump()]}),
        context=ChangeContext(
            derived_spans={"span-1": ["span-1"]},
            verifications={"anc-2": partially_supports()},
        ),
    )
    assert verdict.decision == "reject"
    assert verdict.flags == []
