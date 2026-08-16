"""The parse report survives a restart. ADR-032.

The bug this closes was a known limitation in the README: the report lived only in the
in-process `IngestRegistry`, so after an API restart `/parse` raised for every document
ingested before it. That was tolerable when the report backed one screen the user could
re-trigger by re-uploading. It stopped being tolerable when a conversation started
outliving the process, because a persisted chat about an unpersisted parse is a
conversation the agent cannot continue.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.core.contracts import AbstractSource, ParseFailure, SourceRecord
from app.parsing.arbiter import Arbiter, ArbiterProviders
from app.parsing.pipeline import IngestPipeline
from app.parsing.registry import registry
from app.parsing.reports import InMemoryParseReportStore

FIXTURES = Path(__file__).resolve().parents[1] / "b1" / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[5]
STYLES_DIR = REPO_ROOT / "packages" / "csl-styles"


@pytest.fixture
def tei_xml() -> str:
    return (FIXTURES / "sample.tei.xml").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset():
    registry()._by_doc.clear()  # noqa: SLF001 - process-local state, reset between tests
    yield
    registry()._by_doc.clear()  # noqa: SLF001


class StubGrobid:
    def __init__(self, tei: str = "") -> None:
        self.tei = tei

    async def process_fulltext(self, pdf_bytes: bytes, *, filename: str = "paper.pdf") -> str:  # noqa: ARG002
        return self.tei


class ResolveNothing:
    async def search_works(self, query: str, limit: int = 10) -> list[SourceRecord]:  # noqa: ARG002
        return []

    async def match_reference(self, title: str, year: int | None = None) -> SourceRecord | None:  # noqa: ARG002
        return None

    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]:  # noqa: ARG002
        return None, AbstractSource.UNAVAILABLE

    async def batch_hydrate(self, ids: list[str]) -> list[SourceRecord]:  # noqa: ARG002
        return []


def _pipeline(tei: str, reports: InMemoryParseReportStore | None = None) -> IngestPipeline:
    @asynccontextmanager
    async def report_store_factory():
        yield reports

    class Store:
        async def create(self, document, *, label: str = "ingest"):
            return document.model_copy(update={"version": 1})

    @asynccontextmanager
    async def store_factory():
        yield Store()

    return IngestPipeline(
        grobid=StubGrobid(tei),  # type: ignore[arg-type]
        repair_threshold=0.75,
        ambiguity_margin=0.05,
        styles_dir=STYLES_DIR,
        arbiter=Arbiter(ArbiterProviders(openalex=ResolveNothing()), accept_threshold=0.85),
        store_factory=store_factory,
        report_store_factory=report_store_factory if reports is not None else None,
    )


async def _drain(pipeline: IngestPipeline, doc_id: str, *, timeout: float = 30.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        status = pipeline.status(doc_id)
        if status and status["state"] in {"complete", "failed"}:
            return status
        await asyncio.sleep(0.01)
    raise AssertionError(f"ingest for {doc_id} did not finish within {timeout}s")


async def test_the_report_is_readable_after_the_registry_is_gone(tei_xml: str) -> None:
    """A fresh process, the same database: the report is still there.

    The registry is cleared to stand in for a restart, which is exactly what a restart
    does to it — the rows in `parse_reports` are the only thing left, and they have to be
    enough.
    """
    reports = InMemoryParseReportStore()
    pipeline = _pipeline(tei_xml, reports)
    pipeline.enqueue("doc-restart", "paper.pdf", b"%PDF-1.4")
    await _drain(pipeline, "doc-restart")

    live = await pipeline.parse_report("doc-restart")
    assert live["counts"]["total_detected"] > 0

    registry()._by_doc.clear()  # noqa: SLF001 - the restart
    after_restart = await _pipeline(tei_xml, reports).parse_report("doc-restart")

    assert after_restart["counts"] == live["counts"]
    assert len(after_restart["references"]) == len(live["references"])


async def test_without_a_report_store_the_restart_still_fails_honestly(tei_xml: str) -> None:
    """The pre-ADR-032 behaviour, and it must stay a named refusal rather than an empty
    report — an empty one renders as a paper with no references."""
    pipeline = _pipeline(tei_xml, None)
    pipeline.enqueue("doc-nostore", "paper.pdf", b"%PDF-1.4")
    await _drain(pipeline, "doc-nostore")
    registry()._by_doc.clear()  # noqa: SLF001

    with pytest.raises(ParseFailure, match="no ingest is known"):
        await _pipeline(tei_xml, None).parse_report("doc-nostore")


async def test_the_live_registry_wins_over_the_stored_row(tei_xml: str) -> None:
    """A document being re-ingested right now reports what is happening to it, not what
    the last completed run concluded."""
    reports = InMemoryParseReportStore()
    await reports.put("doc-stale", 1, {"counts": {"total_detected": 999}})

    pipeline = _pipeline(tei_xml, reports)
    pipeline.enqueue("doc-stale", "paper.pdf", b"%PDF-1.4")
    await _drain(pipeline, "doc-stale")

    report = await pipeline.parse_report("doc-stale")
    assert report["counts"]["total_detected"] != 999


async def test_the_stored_report_is_insert_only() -> None:
    """A version that already has a report keeps it. Rewriting would let a later run
    silently restate what an earlier version's bibliography looked like."""
    reports = InMemoryParseReportStore()
    await reports.put("doc-1", 1, {"counts": {"total_detected": 10}})
    await reports.put("doc-1", 1, {"counts": {"total_detected": 20}})

    assert (await reports.get("doc-1", 1))["counts"]["total_detected"] == 10


async def test_the_draft_ir_is_published_before_the_ingest_finishes(tei_xml: str) -> None:
    """ADR-033 at the source: `on_document` fires at `tei_to_ir`, minutes before the
    bibliography is reconciled."""
    seen: list[str] = []
    from app.parsing.pipeline import ingest_tei

    result = await ingest_tei(
        tei_xml,
        doc_id="doc-draft",
        repair_threshold=0.75,
        ambiguity_margin=0.05,
        detect_citation_style=False,
        on_stage=lambda stage: seen.append(stage),
        on_document=lambda document: seen.append(f"document:{len(document.sections)}"),
    )

    assert seen[0].startswith("document:"), "the IR must be published before any later stage"
    assert seen[1] == "references"
    assert result.document.sections
