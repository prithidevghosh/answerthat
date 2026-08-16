"""The tools themselves: draft-IR answering, live introspection, and honest refusals.

The theme is the one §0 keeps returning to — a tool that answers from a hardcoded
description, or that answers confidently about something it has not read, is worse than a
tool that refuses. Each test here pins one place that could quietly become a lie.
"""

from __future__ import annotations

import pytest
from b4_fakes import FakeRetrieval, make_document

from app.core.contracts import ParseFailure
from app.orchestrator.tools import Toolbox

# --------------------------------------------------------------------------- draft IR


async def test_at_stage_references_the_outline_is_served_from_the_draft(
    context, documents, ingest
) -> None:
    """ADR-033. The paper's text is readable minutes before its bibliography is.

    The draft carries the sections and spans; what it does not carry is a reconciled
    bibliography. `is_draft: true` is how the agent knows to say so, and it is on the
    result rather than left to be inferred from the ingest stage.
    """
    documents.by_doc.clear()
    ingest.draft = make_document(version=0, title="A Paper, As Extracted")
    ingest._status = {  # noqa: SLF001 - the fake's own state
        "state": "running",
        "stage": "references",
        "progress": 0.375,
        "version": None,
        "elapsed_s": 12.0,
        "error": None,
    }
    box = Toolbox(context, "doc-1")

    result = await box.get_document_outline("doc-1")

    assert result.ok is True
    assert result.data["is_draft"] is True
    assert result.data["title"] == "A Paper, As Extracted"
    assert "bibliography is still being reconciled" in result.summary


async def test_at_stage_grobid_nothing_is_readable_and_the_tool_says_so(
    context, documents, ingest
) -> None:
    """Before `tei_to_ir`, only the filename is known. No intermediate is fabricated."""
    documents.by_doc.clear()
    ingest.draft = None
    ingest._status = {  # noqa: SLF001
        "state": "running",
        "stage": "grobid",
        "progress": 0.125,
        "version": None,
        "elapsed_s": 3.0,
        "error": None,
    }
    box = Toolbox(context, "doc-1")

    result = await box.get_document_outline("doc-1")

    assert result.ok is False
    assert "not readable yet" in result.error
    assert "'grobid'" in result.error
    assert "Only the filename is known" in result.error


async def test_the_persisted_ir_wins_over_the_draft(context, ingest) -> None:
    """Once a version exists, the draft is stale by definition — it has no source_ids."""
    ingest.draft = make_document(version=0, title="Draft Title")
    box = Toolbox(context, "doc-1")

    result = await box.get_document_outline("doc-1")

    assert result.data["is_draft"] is False
    assert result.data["title"] == "A Paper"


async def test_an_explicit_version_never_falls_back_to_the_draft(context, ingest) -> None:
    """Asking for v9 and getting unversioned draft text is a different document wearing
    the right number."""
    ingest.draft = make_document(version=0)
    box = Toolbox(context, "doc-1")

    result = await box.get_document_outline("doc-1", version=9)

    assert result.ok is False
    assert "has no version 9" in result.error


async def test_read_section_flags_draft_text_too(context, documents, ingest) -> None:
    documents.by_doc.clear()
    ingest.draft = make_document(version=0)
    box = Toolbox(context, "doc-1")

    result = await box.read_section("doc-1", "sec-1")

    assert result.ok is True
    assert result.data["is_draft"] is True
    assert "references not yet reconciled" in result.summary


# --------------------------------------------------------------------------- review plan


async def test_describe_review_plan_reports_three_strategies_without_an_s2_key(context) -> None:
    """The plan is introspected, not written down.

    Asserted against what `CandidateGenerator.strategies_for` would return rather than
    against a fixed string, because the whole point of this tool is that the answer
    differs per document and per deployment. An absent Semantic Scholar key must show up
    as one fewer strategy — not as thinner results, which look identical in the findings
    list and only one of which is our fault.
    """
    context.retrieval = FakeRetrieval(
        ["s2_recommendations", "openalex_search", "openalex_graph"], ["s2_snippet"]
    )
    box = Toolbox(context, "doc-1")

    result = await box.describe_review_plan("doc-1")

    assert result.data["strategies"] == [
        "s2_recommendations",
        "openalex_search",
        "openalex_graph",
    ]
    assert result.data["strategies_unavailable"] == ["s2_snippet"]
    assert "3 of 4 retrieval strategies" in result.summary


async def test_describe_review_plan_reports_four_strategies_with_one(context) -> None:
    box = Toolbox(context, "doc-1")
    result = await box.describe_review_plan("doc-1")

    assert len(result.data["strategies"]) == 4
    assert result.data["strategies_unavailable"] == []
    assert "4 of 4 retrieval strategies" in result.summary


async def test_the_plan_reads_thresholds_from_settings_not_from_prose(context, settings) -> None:
    settings.rerank_keep = 15
    settings.verify_keep = 5
    settings.citability_min = 0.42
    box = Toolbox(context, "doc-1")

    result = await box.describe_review_plan("doc-1")

    assert result.data["thresholds"] == {
        "rerank_keep": 15,
        "verify_keep": 5,
        "citability_min": 0.42,
    }
    assert "Reranker keeps 15" in result.summary


async def test_the_plan_refuses_to_guess_a_claim_count(context) -> None:
    """A guessed "about 40 claims" that delivers 12 is a fabricated number in the one tool
    whose purpose is to replace fabricated numbers with introspected ones."""
    box = Toolbox(context, "doc-1")
    result = await box.describe_review_plan("doc-1")

    assert result.data["claims_estimate"] is None
    assert "not knowable cheaply" in result.data["claims_estimate_note"]


# --------------------------------------------------------------------------- refusals


async def test_the_parse_report_refuses_while_the_ingest_runs(context, ingest) -> None:
    """A half-report reads as a paper with few references."""
    ingest.failure = ParseFailure("ingest for 'doc-1' is still running at stage 'arbiter'")
    box = Toolbox(context, "doc-1")

    result = await box.get_parse_report("doc-1")

    assert result.ok is False
    assert "still running" in result.error


async def test_a_tool_naming_another_document_is_refused(context) -> None:
    """One conversation, one paper. A different id is a hallucination or a confusion, and
    answering about the wrong manuscript is the worst outcome of either."""
    box = Toolbox(context, "doc-1")

    result = await box.get_parse_progress("doc-99")

    assert result.ok is False
    assert "doc-1" in result.error and "doc-99" in result.error


async def test_a_finding_id_that_does_not_exist_is_refused(context) -> None:
    box = Toolbox(context, "doc-1")
    result = await box.get_finding("doc-1", "fnd_invented")

    assert result.ok is False
    assert "do not construct one" in result.error


# --------------------------------------------------------------------------- counters


async def test_review_progress_carries_every_counter(context, review) -> None:
    """"4 findings" and "4 findings, 11 killed on the quote check, 6 abstracts missing"
    are different reports of the same run, and only the second explains itself."""
    review._status = {  # noqa: SLF001
        "status": "complete",
        "verified": 40,
        "total": 40,
        "findings_emitted": 4,
        "candidates_considered": 37,
        "quote_check_failures": 11,
        "unverifiable_no_abstract": 6,
        "claims_without_candidates": 2,
    }
    box = Toolbox(context, "doc-1")

    result = await box.get_review_progress("doc-1")

    for number in ("40", "4 finding", "37 candidate", "11 discarded", "6 had no", "2 claim"):
        assert number in result.summary
    assert result.data["quote_check_failures"] == 11


async def test_findings_listed_during_a_running_review_say_the_list_is_not_final(
    context, review
) -> None:
    review._status = {"status": "running", "verified": 3, "total": 40}  # noqa: SLF001
    review._findings = [  # noqa: SLF001
        {
            "finding_id": "fnd_1",
            "kind": "missing_work",
            "severity": "high",
            "source_id": "s2:aaa",
            "claim": {"claim_id": "clm_1", "text": "A claim.", "citability": 0.9},
            "verification": {"label": "supports"},
        }
    ]
    box = Toolbox(context, "doc-1")

    result = await box.list_findings("doc-1")

    assert "still running" in result.summary
    assert result.data["review_complete"] is False


async def test_no_review_started_is_not_the_same_as_no_findings(context) -> None:
    box = Toolbox(context, "doc-1")
    result = await box.get_review_progress("doc-1")

    assert result.ok is True
    assert result.data["status"] == "not_started"
    assert "No review has been started" in result.summary


# --------------------------------------------------------------------------- export


async def test_the_manifest_states_the_placeholder_disclosure(context) -> None:
    """ADR-008. The exported .tex is not a drop-in replacement, and the user is told
    before they download it, not after."""
    box = Toolbox(context, "doc-1")

    result = await box.get_export_manifest("doc-1")

    assert "2 figure" in result.data["placeholder_disclosure"]
    assert "1 table" in result.data["placeholder_disclosure"]
    assert "not a drop-in replacement" in result.data["placeholder_disclosure"]
    assert "visible placeholders" in result.summary


async def test_an_export_refusal_relays_the_exporter_s_own_message(context, exporter) -> None:
    from app.core.errors import ExportFailure

    async def refuse(doc_id: str, version: int | None = None) -> dict:  # noqa: ARG001
        raise ExportFailure("no citation style selected, so the bibliography cannot render")

    exporter.to_latex = refuse
    box = Toolbox(context, "doc-1")

    result = await box.export_latex("doc-1")

    assert result.ok is False
    assert result.error == "no citation style selected, so the bibliography cannot render"


# --------------------------------------------------------------------------- schemas


def test_every_tool_schema_is_strict_and_fully_required(registry) -> None:
    """OpenAI's strict mode rejects a schema with an un-required property at *call* time.

    A long way from the mistake, and invisible until a model tries to use the tool — so
    the shape is asserted here rather than discovered in production.
    """
    for schema in registry.schemas():
        function = schema["function"]
        assert function["strict"] is True, function["name"]
        parameters = function["parameters"]
        assert parameters["additionalProperties"] is False, function["name"]
        assert sorted(parameters["required"]) == sorted(parameters["properties"]), function["name"]


@pytest.mark.parametrize(
    ("name", "mutating", "confirm"),
    [
        ("commit_change_set", True, True),
        ("revert_document", True, True),
        ("export_latex", True, True),
        ("set_style", True, False),
        ("propose_edit", False, False),
        ("get_parse_report", False, False),
    ],
)
def test_tool_policy_is_what_it_claims(registry, name: str, mutating: bool, confirm: bool) -> None:
    """`propose_edit` is deliberately not mutating: it writes nothing, and marking it so
    would serialise it behind the gate for no reason. `set_style` is mutating but not
    confirmable: the user naming a style *is* the decision, and asking them to confirm the
    thing they just said would be ceremony."""
    tool = registry.get(name)
    assert (tool.mutating, tool.confirm) == (mutating, confirm)


def test_every_description_says_when_not_to_use_the_tool(registry) -> None:
    """The second half of a description is what stops the agent calling `get_parse_report`
    every turn or reaching for search when it already holds the id."""
    for name in registry.names():
        description = registry.get(name).description
        assert len(description) > 120, f"{name} has a description too thin to route on"
