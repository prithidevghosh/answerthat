"""The service surface the API layer consumes: ingest pipeline and style service.

These are the two factories B3 asked for in memory.md §5. The behaviour that matters is
what happens when things are *not* fine: an ingest that failed must not look like an
ingest that found no references, and a pipeline with no arbiter must refuse to be built
by accident.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.core.config import reset_settings_cache
from app.core.contracts import AbstractSource, MissingAPIKeyError, ParseFailure, SourceRecord
from app.core.errors import ConfigurationError, StyleDetectionFailure
from app.core.llm import reset_llm_client
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
ACCEPT = 0.85
MARGIN = 0.05


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
    repair_trigger = THRESHOLD
    csl_styles_dir = STYLES_DIR
    style_ambiguous_delta = 0.05


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
        ambiguity_margin=MARGIN,
        styles_dir=STYLES_DIR,
        arbiter=Arbiter(ArbiterProviders(openalex=ResolveNothing()), accept_threshold=ACCEPT),
        # These tests exercise parsing, not storage, and run without a Postgres. Stated
        # rather than defaulted: production wiring that forgets the store is a bug, and
        # a shared default here is what let it stay one.
        allow_unpersisted=True,
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
        IngestPipeline(  # type: ignore[arg-type]
            grobid=StubGrobid(), repair_threshold=THRESHOLD, ambiguity_margin=MARGIN
        )


def test_running_unreconciled_must_be_stated_explicitly() -> None:
    pipeline = IngestPipeline(
        grobid=StubGrobid(),  # type: ignore[arg-type]
        repair_threshold=THRESHOLD,
        ambiguity_margin=MARGIN,
        allow_unreconciled=True,
        allow_unpersisted=True,
    )
    assert pipeline.status("nothing") is None


def test_a_pipeline_without_a_store_refuses_to_be_built_by_accident() -> None:
    """The regression this guard exists for.

    `get_ingest_pipeline(settings)` used to be called from the composition root with no
    `store_factory`, and the omission was invisible: `_persist` returned the document's
    own version, so the ingest reported `complete` at `version: 1` while
    `ir_document_versions` stayed empty. The parse inspector then 404'd — "Could not load
    parse results" — on a paper the user had just watched finish parsing.

    Reporting success for a write that never happened is the HR-3 failure exactly, so the
    constructor refuses the configuration rather than trusting every future caller to
    remember. Running in memory is still allowed; it just has to be said out loud.
    """
    with pytest.raises(ConfigurationError, match="without a store_factory"):
        IngestPipeline(
            grobid=StubGrobid(),  # type: ignore[arg-type]
            repair_threshold=THRESHOLD,
            ambiguity_margin=MARGIN,
            arbiter=Arbiter(ArbiterProviders(openalex=ResolveNothing()), accept_threshold=ACCEPT),
        )


async def test_a_completed_ingest_has_persisted_the_document_it_reports(tei_xml: str) -> None:
    """`complete` means the version store has it, not that parsing finished.

    The version reported by `/parse-status` is the version `/parse`, `/documents/{id}` and
    every later edit will ask the store for. If it comes from anywhere other than the
    write itself, the two can disagree — and they did.
    """
    written: list[tuple[str, int]] = []

    class RecordingStore:
        async def create(self, document, *, label: str = "ingest"):
            # The store assigns the version; the pipeline must report what came back
            # rather than what it hoped for.
            stored = document.model_copy(update={"version": 7})
            written.append((stored.doc_id, stored.version))
            return stored

    @asynccontextmanager
    async def store_factory():
        yield RecordingStore()

    pipeline = IngestPipeline(
        grobid=StubGrobid(tei=tei_xml),  # type: ignore[arg-type]
        repair_threshold=THRESHOLD,
        ambiguity_margin=MARGIN,
        styles_dir=STYLES_DIR,
        arbiter=Arbiter(ArbiterProviders(openalex=ResolveNothing()), accept_threshold=ACCEPT),
        store_factory=store_factory,
    )
    pipeline.enqueue("doc-persisted", "paper.pdf", b"%PDF-1.4")
    status = await _drain(pipeline, "doc-persisted")

    assert status["state"] == "complete"
    assert written == [("doc-persisted", 7)], "a complete ingest must have written the IR"
    assert status["version"] == 7, "the reported version must be the one the store assigned"


def test_the_factory_returns_one_pipeline_per_process() -> None:
    first = get_ingest_pipeline(  # type: ignore[arg-type]
        FakeSettings(),
        grobid=StubGrobid(),
        allow_unreconciled=True,
        allow_unrepaired=True,
        allow_unpersisted=True,
    )
    second = get_ingest_pipeline(FakeSettings())
    assert first is second


def test_the_factory_wires_a_segmenter_unless_told_not_to(monkeypatch) -> None:
    """A repair tier that is absent by omission would look exactly like a repair tier
    that ran and found nothing to fix (HR-3 / ADR-003).

    `OPENAI_API_KEY` is emptied explicitly rather than assumed absent. It used to be
    assumed, and the assertion below passed on any missing required key at all — so on a
    developer machine with a populated `.env` it was really testing whichever key that
    `.env` happened to leave blank. Setting the condition under test makes the test mean
    the same thing here, in CI, and after ADR-010a shortened the required set.
    """
    sentinel = object()
    pipeline = get_ingest_pipeline(  # type: ignore[arg-type]
        FakeSettings(),
        grobid=StubGrobid(),
        arbiter=Arbiter(ArbiterProviders(openalex=ResolveNothing()), accept_threshold=ACCEPT),
        allow_unpersisted=True,
        segmenter=sentinel,  # type: ignore[arg-type]
    )
    assert pipeline._segmenter is sentinel

    reset_ingest_pipeline()
    # Empty, not deleted: `Settings` falls back to the repo `.env`, so unsetting the
    # process variable would just re-read a real key from there.
    monkeypatch.setenv("OPENAI_API_KEY", "")
    reset_settings_cache()
    reset_llm_client()
    with pytest.raises(MissingAPIKeyError, match="OPENAI_API_KEY"):
        # Building the default segmenter raises rather than yielding a pipeline whose
        # repair tier quietly does nothing.
        get_ingest_pipeline(  # type: ignore[arg-type]
            FakeSettings(),
            grobid=StubGrobid(),
            arbiter=Arbiter(ArbiterProviders(openalex=ResolveNothing()), accept_threshold=ACCEPT),
        )
    reset_ingest_pipeline()
    reset_settings_cache()
    reset_llm_client()


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


async def test_the_report_says_when_the_repair_tier_did_not_run_at_all(tei_xml: str) -> None:
    """"We did not try" and "we tried and nothing qualified" are different claims.

    Collapsing them would show a bibliography whose low-confidence entries were never
    put through the substring check as though they had passed it (HR-3 / ADR-003).
    """
    pipeline = _pipeline(StubGrobid(tei=tei_xml))  # built with no segmenter
    pipeline.enqueue("doc_svc_repair", "paper.pdf", b"%PDF-fake")
    await _drain(pipeline, "doc_svc_repair")

    report = pipeline.parse_report("doc_svc_repair")
    assert report["repairs"] == []
    assert "did not run" in (report["repair_skipped_reason"] or "")


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


@pytest.mark.skipif(not pandoc_available(), reason="pandoc is not installed")
async def test_detection_is_not_rescored_on_every_read(tei_xml: str, monkeypatch) -> None:
    """`GET /parse` calls `detect`, and the parse, review and edit screens all load it.

    Scoring renders the sample through every candidate `.csl` with Pandoc — 61s for a
    40-reference paper, measured — so recomputing per read made every screen cost a
    minute to open. The ingest already scored these references and left the result on the
    report, so the first read reuses it and later reads hit the memo.
    """
    pipeline = _pipeline(StubGrobid(tei=tei_xml))
    pipeline.enqueue("doc_cached", "paper.pdf", b"%PDF-fake")
    await _drain(pipeline, "doc_cached")

    from app.parsing import style as style_module

    calls = []
    real = style_module.detect_style
    monkeypatch.setattr(
        style_module,
        "detect_style",
        lambda *a, **k: (calls.append(1), real(*a, **k))[1],
    )

    service = get_style_service(FakeSettings())
    first = service.detect("doc_cached")
    second = service.detect("doc_cached")

    assert first == second
    assert calls == [], "the ingest's own scoring should be reused, not repeated"


@pytest.mark.skipif(not pandoc_available(), reason="pandoc is not installed")
async def test_repaired_references_are_rescored_rather_than_served_from_the_memo(
    tei_xml: str, monkeypatch
) -> None:
    """The memo must not outlive the data it was computed from.

    Caching on `doc_id` alone would pin the first answer forever, so a reference repaired
    or re-arbitrated after the fact would keep scoring against its old raw string. The key
    is a fingerprint of the references actually scored.

    Asserted on whether scoring *ran*, not on whether the number moved: a changed
    reference that falls outside the scorable sample correctly yields the same score, and
    asserting on the output would call that a cache bug when it is the right answer.
    """
    pipeline = _pipeline(StubGrobid(tei=tei_xml))
    pipeline.enqueue("doc_repair", "paper.pdf", b"%PDF-fake")
    await _drain(pipeline, "doc_repair")

    from app.parsing import style as style_module

    calls = []
    real = style_module.detect_style
    monkeypatch.setattr(
        style_module,
        "detect_style",
        lambda *a, **k: (calls.append(1), real(*a, **k))[1],
    )

    service = get_style_service(FakeSettings())
    service.detect("doc_repair")
    assert calls == [], "the first read reuses the ingest's own scoring"

    # Stand in for a repair pass: the raw string the scorer compares against changes.
    pipeline.result("doc_repair").references[0].raw_string = "A different reference, 2024."

    service.detect("doc_repair")
    assert calls == [1], "a changed reference must invalidate the memo and re-score"


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
        ambiguity_margin=MARGIN,
        detect_citation_style=False,
    )
    json.dumps(build_parse_report(result))
