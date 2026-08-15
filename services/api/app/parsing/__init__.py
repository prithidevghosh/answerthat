"""Parsing: PDF → TEI → Document IR, references reconciled against the real world.

The cascade is ADR-001: GROBID primary → constrained LLM repair for low-confidence
entries only (ADR-003) → arbiter reconciling every entry against Crossref, Semantic
Scholar and OpenAlex. Style detection (ADR-011) runs last, over the reconciled records.

**HR-1: nothing in this package writes to `source_store`.** We hold `source_id`s that
provider adapters created from real HTTP responses.
"""

from app.parsing.agreement import AgreementBreakdown, score_agreement
from app.parsing.arbiter import Arbiter, ArbiterProviders, Reconciliation
from app.parsing.csl import biblstruct_to_csl, parse_confidence_for
from app.parsing.grobid import GrobidClient, GrobidOptions
from app.parsing.models import Coordinate, OrphanMarker, ParsedDocument, TierCounts
from app.parsing.pipeline import IngestResult, attach_source_ids, ingest_pdf, ingest_tei
from app.parsing.references import extract_references, references_from_tei
from app.parsing.repair import (
    ReferenceSegmenter,
    RepairOutcome,
    check_substring_containment,
    repair_references,
)
from app.parsing.style import StyleDetectionResult, classify_marker_family, detect_style
from app.parsing.tei import parse_tei, tei_to_ir
from app.parsing.tiers import initial_tier, tier_for

__all__ = [
    "GrobidClient", "GrobidOptions",
    "parse_tei", "tei_to_ir",
    "biblstruct_to_csl", "parse_confidence_for",
    "extract_references", "references_from_tei",
    "ReferenceSegmenter", "RepairOutcome", "repair_references", "check_substring_containment",
    "Arbiter", "ArbiterProviders", "Reconciliation",
    "AgreementBreakdown", "score_agreement",
    "detect_style", "classify_marker_family", "StyleDetectionResult",
    "initial_tier", "tier_for",
    "ParsedDocument", "OrphanMarker", "Coordinate", "TierCounts",
    "IngestResult", "ingest_pdf", "ingest_tei", "attach_source_ids",
]
