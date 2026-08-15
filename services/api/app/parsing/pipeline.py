"""The ingest pipeline: PDF → TEI → IR → repaired → reconciled → styled.

    GROBID  →  TEI→IR  →  biblStruct→CSL  →  repair tier  →  arbiter  →  style detection
                                             (below threshold only)     (ADR-011)

Two steps here are easy to overlook and both are load-bearing.

**Anchors get their `source_id`s only after arbitration.** Up to that point an anchor
knows *which reference* it points at (GROBID's linkage) but there is no record for it to
be a foreign key into. Attaching the id is the last step, and it happens only for
references the arbiter actually resolved — HR-1 means the id has to have come from a
provider's HTTP response, not from us.

**The tier invariant is asserted, in code, at the end.** Not logged, not checked in a
test only: asserted here, on every ingest, so a reference that goes missing between
GROBID and the UI fails loudly instead of shipping as a shorter bibliography.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.contracts import ConfidenceTier, Document, ParsedReference
from app.core.errors import StyleDetectionFailure
from app.ir import ids
from app.ir.traversal import iter_anchors
from app.parsing.arbiter import Arbiter, Reconciliation
from app.parsing.grobid import GrobidClient
from app.parsing.models import ParsedDocument, TierCounts
from app.parsing.references import references_from_tei
from app.parsing.repair import ReferenceSegmenter, RepairOutcome, repair_references
from app.parsing.style import StyleDetectionResult, detect_style
from app.parsing.tei import parse_tei, tei_to_ir

__all__ = ["IngestResult", "ingest_tei", "ingest_pdf", "attach_source_ids"]


@dataclass
class IngestResult:
    parsed: ParsedDocument
    references: list[ParsedReference]
    reconciliations: list[Reconciliation] = field(default_factory=list)
    repairs: list[RepairOutcome] = field(default_factory=list)
    style: StyleDetectionResult | None = None
    # Set when style detection could not run at all. The document is still valid — it
    # just has no detected style, and the user is told so rather than being handed a
    # silently chosen default.
    style_error: str = ""

    @property
    def document(self) -> Document:
        return self.parsed.document

    def tier_counts(self) -> TierCounts:
        counts = TierCounts(total_detected=len(self.references))
        for reference in self.references:
            match reference.tier:
                case ConfidenceTier.RESOLVED:
                    counts.resolved += 1
                case ConfidenceTier.PARSED_UNRESOLVED:
                    counts.parsed_unresolved += 1
                case ConfidenceTier.LOW_CONFIDENCE:
                    counts.low_confidence += 1
                case ConfidenceTier.QUARANTINED:
                    counts.quarantined += 1
                case ConfidenceTier.ORPHAN_MARKER:  # pragma: no cover - not a reference tier
                    counts.total_detected -= 1
        counts.orphan_marker = len(self.parsed.orphan_markers)
        return counts

    def resolved_sources(self) -> dict[str, dict]:
        """`source_id` → canonical CSL-JSON, ready to hand to the exporter."""
        return {
            r.source_id: r.csl
            for r in self.references
            if r.source_id and r.csl and r.tier == ConfidenceTier.RESOLVED
        }


def attach_source_ids(parsed: ParsedDocument, references: list[ParsedReference]) -> int:
    """Point every anchor at the record its reference resolved to.

    An anchor whose reference did not resolve keeps an empty `source_ids` list. That is
    the honest state and it is visible downstream: the marker is there, we know which
    reference it belongs to, and we have no record to link it to.
    """
    by_ref = {r.ref_id: r for r in references if r.source_id}
    attached = 0
    for anchor_ref in iter_anchors(parsed.document):
        ref_id = parsed.anchor_to_ref.get(anchor_ref.anchor.anchor_id)
        if not ref_id:
            continue
        reference = by_ref.get(ref_id)
        if reference is None or not reference.source_id:
            continue
        if reference.source_id not in anchor_ref.anchor.source_ids:
            anchor_ref.anchor.source_ids.append(reference.source_id)
            attached += 1
    return attached


async def ingest_tei(
    tei_xml: str,
    *,
    doc_id: str,
    repair_threshold: float,
    segmenter: ReferenceSegmenter | None = None,
    arbiter: Arbiter | None = None,
    styles_dir: Path | None = None,
    ambiguity_margin: float = 0.05,
    detect_citation_style: bool = True,
) -> IngestResult:
    """Everything downstream of GROBID. Separated so it is testable without a sidecar."""
    parsed = tei_to_ir(tei_xml, doc_id=doc_id)
    references = references_from_tei(parse_tei(tei_xml), threshold=repair_threshold)

    repairs: list[RepairOutcome] = []
    if segmenter is not None:
        references, repairs = await repair_references(
            references, segmenter, threshold=repair_threshold
        )

    reconciliations: list[Reconciliation] = []
    if arbiter is not None:
        references, reconciliations = await arbiter.reconcile(references)

    attach_source_ids(parsed, references)
    parsed.references = references

    result = IngestResult(
        parsed=parsed,
        references=references,
        reconciliations=reconciliations,
        repairs=repairs,
    )

    if detect_citation_style:
        markers = [
            a.anchor.original_marker_text or "" for a in iter_anchors(parsed.document)
        ]
        try:
            result.style = detect_style(
                references,
                markers,
                styles_dir=styles_dir,
                ambiguity_margin=ambiguity_margin,
            )
        except StyleDetectionFailure as exc:
            # Surfaced, not swallowed: the document keeps no style and says why.
            result.style_error = str(exc)
        else:
            parsed.document.metadata.style_id = result.style.style_id
            parsed.document.metadata.style_confidence = result.style.similarity
            parsed.document.metadata.style_ambiguous = result.style.ambiguous

    # HR-3, asserted on every ingest rather than only in a test.
    result.tier_counts().assert_invariant()
    return result


async def ingest_pdf(
    pdf_bytes: bytes,
    *,
    doc_id: str | None = None,
    filename: str = "paper.pdf",
    grobid: GrobidClient,
    repair_threshold: float,
    segmenter: ReferenceSegmenter | None = None,
    arbiter: Arbiter | None = None,
    styles_dir: Path | None = None,
    ambiguity_margin: float = 0.05,
) -> IngestResult:
    """The full path from an uploaded PDF to a reconciled Document IR."""
    resolved_doc_id = doc_id or ids.new_id(ids.DOCUMENT)
    tei_xml = await grobid.process_fulltext(pdf_bytes, filename=filename)
    return await ingest_tei(
        tei_xml,
        doc_id=resolved_doc_id,
        repair_threshold=repair_threshold,
        segmenter=segmenter,
        arbiter=arbiter,
        styles_dir=styles_dir,
        ambiguity_margin=ambiguity_margin,
    )
