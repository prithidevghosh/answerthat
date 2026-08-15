"""The service surface the API layer consumes: ingest pipeline and style service.

These are the two factories B3 asked for in memory.md §5. The behaviour that matters is
what happens when things are *not* fine: an ingest that failed must not look like an
ingest that found no references, and a pipeline with no arbiter must refuse to be built
by accident.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.contracts import AbstractSource, ParseFailure, SourceRecord
from app.core.errors import ConfigurationError, StyleDetectionFailure
from app.export.pandoc import pandoc_available
from app.parsing.arbiter import Arbiter, ArbiterProviders
from app.parsing.pipeline import (
    IngestPipeline,
    build_parse_report,
    get_ingest_pipeline,
    ingest_tei,
    reset_ingest_pipeline,
)
from app.parsing.registry import registry
from app.parsing.style import get_style_service, reset_style_service

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[5]
STYLES_DIR = REPO_ROOT / "packages" / "csl-styles"
THRESHOLD = 0.75


@pytest.fixture
def tei_xml() -> str:
    return (FIXTURES / "sample.tei.xml").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset():
    reset_ingest_pipeline()
    reset_style_service()
    registry()._by_doc.clear()  # noqa: SLF001 - process-local state, reset between tests
    yield
    reset_ingest_pipeline()
    reset_style_service()


class FakeSettings:
    repair_confidence_threshold = THRESHOLD
    csl_styles_dir = STYLES_DIR
    style_ambiguity_margin = 0.05


class StubGrobid:
    """Stands in for the sidecar. Either returns TEI or fails like GROBID would."""

    def __init__(self, tei: str = "", error: Exception | None = None) -> None:
        self.tei = tei
        self.error = error
        self.calls: list[str] = []

    async def process_fulltext(self, pdf_bytes: bytes, *, filename: str = "paper.pdf") -> str:
        self.calls.append(filename)
        if self.error:
            raise self.error
        return self.tei


class ResolveNothing:
    async def search_works(self, query: str, limit: int = 10) -> list[SourceRecord]:
        return []

    async def match_reference(self, title: str, year: int | None = None) -> SourceRecord | None:
        return None

    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]:
        return None, AbstractSource.UNAVAILABLE

    async def batch_hydrate(self, ids: list[str]) -> list[SourceRecord]:
        return []


def _pipeline(grobid: StubGrobid, **kwargs) -> IngestPipeline:
    return IngestPipeline(
        grobid=grobid,  # type: ignore[arg-type]
        repair_threshold=THRESHOLD,
        styles_dir=STYLES_DIR,
        arbiter=Arbiter(ArbiterProviders(openalex=ResolveNothing())),
        **kwargs,
    )


async def _drain(pipeline: IngestPipeline, doc_id: str, *, timeout: float = 30.0) -> dict:
    """Wait for the background ingest to reach a terminal state."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        status = pipeline.status(doc_id)
        if status and status["state"] in {"complete", "failed"}:
            return status
        await asyncio.sleep(0.01)
    raise AssertionError(f"ingest for {doc_id} did not finish within {timeout}s")


# ---------------------------------------------------------------- construction


def test_a_pipeline_without_an_arbiter_refuses_to_be_built_by_accident() -> None:
    """Every reference would come back unresolved, which reads as a bad paper rather
    than as missing configuration (HR-3 / ADR-010)."""
    with pytest.raises(ConfigurationError, match="without an arbiter"):
        IngestPipeline(grobid=StubGrobid(), repair_threshold=THRESHOLD)  # type: ignore[arg-type]


def test_running_unreconciled_must_be_stated_explicitly() -> None:
    pipeline = IngestPipeline(
        grobid=StubGrobid(),  # type: ignore[arg-type]
        repair_threshold=THRESHOLD,
        allow_unreconciled=True,
    )
    assert pipeline.status("nothing") is None


def test_the_factory_returns_one_pipeline_per_process() -> None:
    first = get_ingest_pipeline(FakeSettings(), grobid=StubGrobid(), allow_unreconciled=True)  # type: ignore[arg-type]
    second = get_ingest_pipeline(FakeSettings())
    assert first is second


# ---------------------------------------------------------------- happy path


async def test_enqueue_runs_the_ingest_and_reports_progress(tei_xml: str) -> None:
    pipeline = _pipeline(StubGrobid(tei=tei_xml))
    job_id = pipeline.enqueue("doc_svc1", "paper.pdf", b"%PDF-fake")
    assert job_id.startswith("job_")

    status = await _drain(pipeline, "doc_svc1")
    assert status["state"] == "complete"
    assert status["stage"] == "complete"
    assert status["progress"] == 1.0
    assert status["version"] == 1
    assert status["error"] is None


async def test_parse_report_carries_references_orphans_and_counts(tei_xml: str) -> None:
    pipeline = _pipeline(StubGrobid(tei=tei_xml))
    pipeline.enqueue("doc_svc2", "paper.pdf", b"%PDF-fake")
    await _drain(pipeline, "doc_svc2")

    report = pipeline.parse_report("doc_svc2")
    assert len(report["references"]) == 4
    assert len(report["orphan_markers"]) == 1
    assert report["orphan_markers"][0]["marker_text"] == "[42]"

    counts = report["counts"]
    assert counts["total_detected"] == 4
    assert counts["accounted_for"] == 4
    assert counts["orphan_marker"] == 1


async def test_the_report_shows_why_a_reference_did_not_resolve(tei_xml: str) -> None:
    """HR-3: the parse inspector explains, it does not just colour things red."""
    pipeline = _pipeline(StubGrobid(tei=tei_xml))
    pipeline.enqueue("doc_svc3", "paper.pdf", b"%PDF-fake")
    await _drain(pipeline, "doc_svc3")

    report = pipeline.parse_report("doc_svc3")
    reconciliation = next(r for r in report["reconciliations"] if r["ref_id"] == "b0")
    assert not reconciliation["accepted"]
    assert reconciliation["fully_checked"] is True
    assert reconciliation["notes"]
    assert reconciliation["provisional_csl"]["title"] == "Attention Is All You Need"


async def test_references_carry_their_anchor_ids_and_coordinates(tei_xml: str) -> None:
    pipeline = _pipeline(StubGrobid(tei=tei_xml))
    pipeline.enqueue("doc_svc4", "paper.pdf", b"%PDF-fake")
    await _drain(pipeline, "doc_svc4")

    report = pipeline.parse_report("doc_svc4")
    b0 = next(r for r in report["references"] if r["ref_id"] == "b0")
    assert len(b0["anchor_ids"]) == 2
    assert b0["coordinates"][0]["page"] == 5


# ---------------------------------------------------------------- failure paths


async def test_a_failed_ingest_is_reported_as_failed_not_as_empty(tei_xml: str) -> None:
    """The distinction the whole parse inspector rests on."""
    pipeline = _pipeline(StubGrobid(error=RuntimeError("GROBID exploded")))
    pipeline.enqueue("doc_bad", "paper.pdf", b"%PDF-fake")
    status = await _drain(pipeline, "doc_bad")

    assert status["state"] == "failed"
    assert "GROBID exploded" in status["error"]
    with pytest.raises(ParseFailure, match="ingest failed"):
        pipeline.parse_report("doc_bad")


def test_parse_report_for_an_unknown_document_raises() -> None:
    pipeline = _pipeline(StubGrobid())
    with pytest.raises(ParseFailure, match="no ingest is known"):
        pipeline.parse_report("doc_never_seen")


async def test_parse_report_refuses_while_the_ingest_is_still_running(tei_xml: str) -> None:
    """An empty report for a running job would render as 'this paper has no references'."""
    pipeline = _pipeline(StubGrobid(tei=tei_xml))
    pipeline.enqueue("doc_slow", "paper.pdf", b"%PDF-fake")
    with pytest.raises(ParseFailure, match="still"):
        pipeline.parse_report("doc_slow")
    await _drain(pipeline, "doc_slow")


def test_record_failure_creates_a_visible_record_even_for_an_unknown_document() -> None:
    pipeline = _pipeline(StubGrobid())
    pipeline.record_failure("doc_ghost", "upload was not a PDF")
    status = pipeline.status("doc_ghost")
    assert status is not None
    assert status["state"] == "failed"
    assert status["error"] == "upload was not a PDF"


def test_status_is_none_for_a_document_that_was_never_ingested() -> None:
    """Not an empty status object — the API must be able to answer 404, not 'queued'."""
    assert _pipeline(StubGrobid()).status("doc_nope") is None


# ---------------------------------------------------------------- style service


@pytest.mark.skipif(not pandoc_available(), reason="pandoc is not installed")
async def test_style_service_detects_from_the_completed_ingest(tei_xml: str) -> None:
    pipeline = _pipeline(StubGrobid(tei=tei_xml))
    pipeline.enqueue("doc_style", "paper.pdf", b"%PDF-fake")
    await _drain(pipeline, "doc_style")

    payload = get_style_service(FakeSettings()).detect("doc_style")
    assert payload["marker_family"]["family"] == "numeric"
    assert payload["candidates"]
    assert len(payload["shortlist"]) == 6
    assert payload["score"] is not None
    assert not payload["chosen_by_user"]


def test_style_service_refuses_before_the_ingest_exists() -> None:
    with pytest.raises(StyleDetectionFailure, match="no completed ingest"):
        get_style_service(FakeSettings()).detect("doc_absent")


def test_style_selection_validates_against_the_shortlist() -> None:
    with pytest.raises(StyleDetectionFailure, match="unknown style_id"):
        get_style_service(FakeSettings()).select("doc_any", "harvard-imaginary")


@pytest.mark.skipif(not pandoc_available(), reason="pandoc is not installed")
async def test_a_user_choice_overrides_detection_and_clears_ambiguity(tei_xml: str) -> None:
    """The required path out of an `ambiguous` result."""
    pipeline = _pipeline(StubGrobid(tei=tei_xml))
    pipeline.enqueue("doc_pick", "paper.pdf", b"%PDF-fake")
    await _drain(pipeline, "doc_pick")

    service = get_style_service(FakeSettings())
    chosen = service.select("doc_pick", "vancouver")
    assert chosen["style_id"] == "vancouver"
    assert chosen["ambiguous"] is False
    assert chosen["chosen_by_user"] is True

    document = pipeline.result("doc_pick").document
    assert document.metadata.style_id == "vancouver"
    assert document.metadata.style_ambiguous is False

    again = service.detect("doc_pick")
    assert again["style_id"] == "vancouver"
    assert again["chosen_by_user"] is True


# ---------------------------------------------------------------- report shape


async def test_build_parse_report_is_json_serialisable(tei_xml: str) -> None:
    import json

    result = await ingest_tei(
        tei_xml,
        doc_id="doc_json",
        repair_threshold=THRESHOLD,
        detect_citation_style=False,
    )
    json.dumps(build_parse_report(result))
