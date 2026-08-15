"""The ingest pipeline end to end, from TEI to a reconciled, exportable document.

This is where CP-2's invariant is exercised against a whole document rather than a
hand-built list: every reference GROBID detected must land in exactly one tier, the
orphan marker must survive as an orphan marker, and the anchors of resolved references
must end up carrying real `source_id`s while unresolved ones honestly carry none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.contracts import (
    AbstractSource,
    ConfidenceTier,
    Provenance,
    SourceRecord,
)
from app.export.pandoc import pandoc_available
from app.export.roundtrip import verify_round_trip
from app.ir import traversal as tv
from app.ir.store import InMemoryDocumentStore
from app.parsing.arbiter import Arbiter, ArbiterProviders
from app.parsing.pipeline import ingest_tei

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[5]
STYLES_DIR = REPO_ROOT / "packages" / "csl-styles"
THRESHOLD = 0.75


@pytest.fixture
def tei_xml() -> str:
    return (FIXTURES / "sample.tei.xml").read_text(encoding="utf-8")


def _record(source_id: str, csl: dict) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        csl=csl,
        provenance=Provenance(
            provider="openalex",
            endpoint="/works",
            retrieved_at="2026-08-15T00:00:00Z",
            external_url=f"https://openalex.org/{source_id}",
        ),
        abstract_source=AbstractSource.UNAVAILABLE,
    )


class OneHitProvider:
    """Resolves exactly the Vaswani reference and nothing else.

    A stub that resolves everything would hide the interesting half of the pipeline:
    what a document looks like when only some of its references resolve.
    """

    def __init__(self) -> None:
        self.record = _record(
            "oa:W2963403868",
            {
                "type": "paper-conference",
                "title": "Attention Is All You Need",
                "author": [{"family": "Vaswani", "given": "Ashish"}],
                "issued": {"date-parts": [[2017]]},
                "container-title": "Advances in Neural Information Processing Systems",
                "page": "5998-6008",
            },
        )

    async def search_works(self, query: str, limit: int = 10) -> list[SourceRecord]:
        return [self.record] if "attention" in query.casefold() else []

    async def match_reference(self, title: str, year: int | None = None) -> SourceRecord | None:
        return self.record if "attention" in title.casefold() else None

    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]:
        return None, AbstractSource.UNAVAILABLE

    async def batch_hydrate(self, ids: list[str]) -> list[SourceRecord]:
        return []


async def _ingest(tei_xml: str, *, with_arbiter: bool = True, style: bool = False):
    arbiter = (
        Arbiter(ArbiterProviders(openalex=OneHitProvider()), accept_threshold=0.85)
        if with_arbiter
        else None
    )
    return await ingest_tei(
        tei_xml,
        doc_id="doc_pipeline",
        repair_threshold=THRESHOLD,
        arbiter=arbiter,
        styles_dir=STYLES_DIR,
        detect_citation_style=style,
    )


# ---------------------------------------------------------------- tiers and the invariant


async def test_every_reference_lands_in_exactly_one_tier(tei_xml: str) -> None:
    """CP-2: resolved + parsed_unresolved + low_confidence + quarantined == detected."""
    result = await _ingest(tei_xml)
    counts = result.tier_counts()
    assert counts.total_detected == 4
    assert counts.accounted_for == counts.total_detected
    counts.assert_invariant()


async def test_all_five_tiers_are_reachable(tei_xml: str) -> None:
    result = await _ingest(tei_xml)
    counts = result.tier_counts()
    assert counts.resolved == 1  # b0, via the stub provider
    assert counts.quarantined == 1  # b3, the unparseable fragment
    assert counts.parsed_unresolved + counts.low_confidence == 2  # b1, b2
    assert counts.orphan_marker == 1  # the [42] marker pointing at #b99


async def test_the_invariant_actually_fires_when_a_reference_goes_missing(tei_xml: str) -> None:
    """An invariant that cannot fail is decoration."""
    result = await _ingest(tei_xml)
    counts = result.tier_counts()
    counts.resolved -= 1
    with pytest.raises(AssertionError, match="No reference is ever dropped"):
        counts.assert_invariant()


async def test_orphan_markers_are_not_counted_as_references(tei_xml: str) -> None:
    """An orphan marker has no biblStruct behind it; counting it would mask a real leak."""
    result = await _ingest(tei_xml)
    counts = result.tier_counts()
    assert counts.orphan_marker == 1
    assert counts.accounted_for == 4, "the orphan must not inflate the reference total"


async def test_quarantined_entry_keeps_its_raw_string_verbatim(tei_xml: str) -> None:
    result = await _ingest(tei_xml)
    quarantined = [r for r in result.references if r.tier == ConfidenceTier.QUARANTINED]
    assert [r.raw_string for r in quarantined] == ["Smith, J. mumble mumble 20??, pp. ??-??"]


# ---------------------------------------------------------------- source_id attachment


async def test_resolved_references_give_their_anchors_a_source_id(tei_xml: str) -> None:
    result = await _ingest(tei_xml)
    resolved = next(r for r in result.references if r.tier == ConfidenceTier.RESOLVED)
    assert resolved.ref_id == "b0"
    assert resolved.source_id == "oa:W2963403868"

    anchor_ids = result.parsed.anchors_for_reference("b0")
    assert len(anchor_ids) == 2
    for anchor_id in anchor_ids:
        anchor = tv.find_anchor(result.document, anchor_id)
        assert anchor is not None
        assert anchor.anchor.source_ids == ["oa:W2963403868"]


async def test_unresolved_references_leave_their_anchors_empty(tei_xml: str) -> None:
    """HR-1: no record, no foreign key. The gap is the honest state, and it is visible."""
    result = await _ingest(tei_xml)
    unresolved = [r for r in result.references if r.tier != ConfidenceTier.RESOLVED]
    for reference in unresolved:
        for anchor_id in result.parsed.anchors_for_reference(reference.ref_id):
            anchor = tv.find_anchor(result.document, anchor_id)
            assert anchor is not None and anchor.anchor.source_ids == []


async def test_the_canonical_record_replaced_our_parse(tei_xml: str) -> None:
    """ADR-001, seen end to end: their page range, our raw string, both retained."""
    result = await _ingest(tei_xml)
    resolved = next(r for r in result.references if r.tier == ConfidenceTier.RESOLVED)
    assert resolved.csl["page"] == "5998-6008"
    assert resolved.raw_string.startswith("A. Vaswani")

    reconciliation = next(r for r in result.reconciliations if r.ref_id == "b0")
    assert reconciliation.provisional_csl["title"] == "Attention Is All You Need"
    assert reconciliation.accepted and reconciliation.agreement.score >= 0.85


async def test_every_source_id_in_the_document_came_from_a_provider(tei_xml: str) -> None:
    """HR-1, structurally: the document's source_ids are a subset of what we were given."""
    result = await _ingest(tei_xml)
    from_providers = {r.source_id for r in result.references if r.source_id}
    in_document = set(tv.source_id_multiset(result.document))
    assert in_document <= from_providers


# ---------------------------------------------------------------- without an arbiter


async def test_nothing_resolves_without_providers(tei_xml: str) -> None:
    result = await _ingest(tei_xml, with_arbiter=False)
    assert result.tier_counts().resolved == 0
    assert all(r.source_id is None for r in result.references)
    result.tier_counts().assert_invariant()


# ---------------------------------------------------------------- style and export


@pytest.mark.skipif(not pandoc_available(), reason="pandoc is not installed")
async def test_style_detection_runs_over_the_reconciled_records(tei_xml: str) -> None:
    result = await _ingest(tei_xml, style=True)
    assert result.style is not None, result.style_error
    assert result.style.marker_family.family == "numeric"
    assert result.document.metadata.style_id == result.style.style_id
    assert result.document.metadata.style_ambiguous == result.style.ambiguous


@pytest.mark.skipif(not pandoc_available(), reason="pandoc is not installed")
async def test_the_ingested_document_survives_the_export_round_trip(tei_xml: str) -> None:
    """CP-1 against a real parse rather than a hand-built fixture."""
    result = await _ingest(tei_xml)
    report = verify_round_trip(
        result.document, result.resolved_sources(), style_id="ieee", styles_dir=STYLES_DIR
    )
    assert report.failures() == [], report.failures()
    assert report.found_title == "Revisiting Quadratic Attention on Modern Hardware"
    assert report.found_sections == ["Abstract", "Introduction", "Experimental Setup", "Figures and Tables"]


async def test_the_ingested_document_persists_with_a_version(tei_xml: str) -> None:
    result = await _ingest(tei_xml)
    store = InMemoryDocumentStore()
    stored = await store.create(result.document)
    assert stored.version == 1
    head = await store.head(result.document.doc_id)
    assert head is not None
    assert len(tv.anchor_ids(head)) == len(tv.anchor_ids(result.document))
