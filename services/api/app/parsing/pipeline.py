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

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.contracts import ConfidenceTier, Document, ParsedReference, ParseFailure
from app.core.errors import ConfigurationError, StyleDetectionFailure
from app.ir import ids
from app.ir.traversal import iter_anchors
from app.parsing.arbiter import Arbiter, ArbiterProviders, Reconciliation
from app.parsing.grobid import GrobidClient
from app.parsing.models import ParsedDocument, TierCounts
from app.parsing.references import references_from_tei
from app.parsing.registry import registry
from app.parsing.repair import ReferenceSegmenter, RepairOutcome, repair_references
from app.parsing.style import StyleDetectionResult, detect_style
from app.parsing.tei import parse_tei, tei_to_ir

__all__ = [
    "IngestResult",
    "ingest_tei",
    "ingest_pdf",
    "attach_source_ids",
    "IngestPipeline",
    "get_ingest_pipeline",
    "build_default_arbiter",
    "reset_ingest_pipeline",
    "build_parse_report",
]


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
    on_stage: Callable[[str], None] | None = None,
) -> IngestResult:
    """Everything downstream of GROBID. Separated so it is testable without a sidecar."""

    def _stage(name: str) -> None:
        if on_stage is not None:
            on_stage(name)

    parsed = tei_to_ir(tei_xml, doc_id=doc_id)
    _stage("references")
    references = references_from_tei(parse_tei(tei_xml), threshold=repair_threshold)

    repairs: list[RepairOutcome] = []
    if segmenter is not None:
        _stage("repair")
        references, repairs = await repair_references(
            references, segmenter, threshold=repair_threshold
        )

    reconciliations: list[Reconciliation] = []
    if arbiter is not None:
        _stage("arbiter")
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
        _stage("style")
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


# ---------------------------------------------------------------------------
# Service surface for the API layer (B3's `get_ingest_pipeline`, memory.md §5).
# ---------------------------------------------------------------------------


class IngestPipeline:
    """Runs ingests in the background and remembers what they produced.

    `providers` is required unless `allow_unreconciled=True` is passed explicitly. That
    is deliberate: without the arbiter every reference comes back `parsed_unresolved`,
    which renders as a paper whose bibliography we could not verify — indistinguishable,
    to a reader, from a paper whose references are genuinely unresolvable. A caller may
    opt into that (tests do), but it never happens by omission (HR-3 / ADR-010).
    """

    def __init__(
        self,
        *,
        grobid: GrobidClient,
        repair_threshold: float,
        styles_dir: Path | None = None,
        ambiguity_margin: float = 0.05,
        arbiter: Arbiter | None = None,
        segmenter: ReferenceSegmenter | None = None,
        store_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
        allow_unreconciled: bool = False,
    ) -> None:
        if arbiter is None and not allow_unreconciled:
            raise ConfigurationError(
                "IngestPipeline was constructed without an arbiter. Every reference would "
                "come back unresolved, which reads to a user as a paper full of unverifiable "
                "references rather than as missing configuration. Pass `arbiter=`, or pass "
                "`allow_unreconciled=True` to state that you meant it."
            )
        self._grobid = grobid
        self._repair_threshold = repair_threshold
        self._styles_dir = styles_dir
        self._ambiguity_margin = ambiguity_margin
        self._arbiter = arbiter
        self._segmenter = segmenter
        self._store_factory = store_factory
        self._registry = registry()
        self._tasks: set[asyncio.Task[None]] = set()

    # -- API surface -------------------------------------------------------

    def enqueue(self, doc_id: str, filename: str, payload: bytes) -> str:
        """Start an ingest in the background and return its job id."""
        job_id = ids.new_id("job")
        self._registry.create(doc_id, job_id, filename)
        task = asyncio.create_task(self._run(doc_id, filename, payload))
        # Hold a reference: a task that nothing holds can be garbage collected
        # mid-flight, and the ingest would vanish with no error anywhere.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job_id

    def status(self, doc_id: str) -> dict[str, Any] | None:
        record = self._registry.get(doc_id)
        return record.status() if record else None

    def result(self, doc_id: str) -> IngestResult | None:
        record = self._registry.get(doc_id)
        return record.result if record else None

    def record_failure(self, doc_id: str, message: str) -> None:
        self._registry.fail(doc_id, message)

    def parse_report(self, doc_id: str) -> dict[str, Any]:
        """References, orphan markers and tier counts for the parse inspector.

        Raises if the ingest is not finished. Returning an empty report for a running
        job would render as "this paper has no references".
        """
        record = self._registry.get(doc_id)
        if record is None:
            raise ParseFailure(f"no ingest is known for document {doc_id!r}")
        if record.state == "failed":
            raise ParseFailure(f"ingest failed for {doc_id!r}: {record.error}")
        if record.result is None:
            raise ParseFailure(
                f"ingest for {doc_id!r} is still {record.state} at stage {record.stage!r}; "
                "poll status until it is complete"
            )
        return build_parse_report(record.result)

    # -- internals ---------------------------------------------------------

    async def _run(self, doc_id: str, filename: str, payload: bytes) -> None:
        try:
            self._registry.advance(doc_id, "grobid")
            tei_xml = await self._grobid.process_fulltext(payload, filename=filename)

            self._registry.advance(doc_id, "tei_to_ir")
            result = await ingest_tei(
                tei_xml,
                doc_id=doc_id,
                repair_threshold=self._repair_threshold,
                segmenter=self._segmenter,
                arbiter=self._arbiter,
                styles_dir=self._styles_dir,
                ambiguity_margin=self._ambiguity_margin,
                on_stage=lambda stage: self._registry.advance(doc_id, stage),
            )

            self._registry.advance(doc_id, "persist")
            version = await self._persist(result.document)
            self._registry.complete(doc_id, result, version)
        except Exception as exc:  # noqa: BLE001 - recorded on the job, then visible via status()
            # Not swallowed: the job goes to `failed` with the reason attached, which is
            # what /parse-status reports and what the UI shows the user (HR-3).
            self._registry.fail(doc_id, f"{type(exc).__name__}: {exc}")

    async def _persist(self, document: Document) -> int:
        if self._store_factory is None:
            # Nothing to persist into — the caller is running in-memory (tests). The
            # version still comes from the document itself rather than being invented.
            return document.version
        async with self._store_factory() as store:
            stored = await store.create(document)
            return stored.version


def build_parse_report(result: IngestResult) -> dict[str, Any]:
    """The parse-inspector payload: every reference, every orphan, and the tier counts."""
    counts = result.tier_counts()
    return {
        "references": [
            {
                **reference.model_dump(mode="json"),
                "anchor_ids": result.parsed.anchors_for_reference(reference.ref_id),
                "coordinates": [
                    box.__dict__ for box in result.parsed.coordinates.get(reference.ref_id, [])
                ],
            }
            for reference in result.references
        ],
        "orphan_markers": [
            {
                "anchor_id": orphan.anchor_id,
                "marker_text": orphan.marker_text,
                "target": orphan.target,
                "section_id": orphan.section_id,
                "span_id": orphan.span_id,
                "page": orphan.page,
                "reason": orphan.reason,
            }
            for orphan in result.parsed.orphan_markers
        ],
        "counts": {
            "total_detected": counts.total_detected,
            "resolved": counts.resolved,
            "parsed_unresolved": counts.parsed_unresolved,
            "low_confidence": counts.low_confidence,
            "quarantined": counts.quarantined,
            "orphan_marker": counts.orphan_marker,
            "accounted_for": counts.accounted_for,
        },
        "reconciliations": [
            {
                "ref_id": r.ref_id,
                "accepted": r.accepted,
                "path": r.path,
                "agreement_score": r.agreement.score if r.agreement else None,
                "matched_on": r.agreement.matched_on if r.agreement else None,
                "source_id": r.source_id,
                "provisional_csl": r.provisional_csl,
                "notes": r.notes,
                "provider_errors": r.provider_errors,
                "fully_checked": r.fully_checked,
            }
            for r in result.reconciliations
        ],
        "style_error": result.style_error or None,
    }


def build_default_arbiter(settings: Any) -> Arbiter:
    """Assemble the arbiter from B2's provider adapters.

    The API layer calls `get_ingest_pipeline(settings)` with settings and nothing else,
    so something has to turn settings into three adapters. This is a *use* of
    `app/providers/` through its public constructors — nothing here writes to
    `source_store`; the adapters do that themselves from their own HTTP responses (HR-1).

    It raises rather than returning a partial set. An arbiter missing its Crossref
    adapter silently stops resolving every DOI-bearing reference in the paper, which
    looks exactly like a bibliography that could not be verified.
    """
    from app.providers.cache import PostgresResponseCache
    from app.providers.crossref import CrossrefProvider
    from app.providers.openalex import OpenAlexProvider
    from app.providers.semantic_scholar import SemanticScholarProvider
    from app.providers.source_store import PostgresSourceStore

    cache = PostgresResponseCache()
    store = PostgresSourceStore()
    return Arbiter(
        ArbiterProviders(
            crossref=CrossrefProvider(
                mailto=settings.openalex_mailto, cache=cache, store=store
            ),
            semantic_scholar=SemanticScholarProvider(
                api_key=settings.semantic_scholar_api_key, cache=cache, store=store
            ),
            openalex=OpenAlexProvider(
                api_key=settings.openalex_api_key,
                mailto=settings.openalex_mailto,
                cache=cache,
                store=store,
            ),
        ),
        accept_threshold=settings.arbiter_accept_threshold,
    )


def get_ingest_pipeline(
    settings: Any,
    *,
    grobid: GrobidClient | None = None,
    arbiter: Arbiter | None = None,
    segmenter: ReferenceSegmenter | None = None,
    store_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    allow_unreconciled: bool = False,
) -> IngestPipeline:
    """Factory for the API layer. One pipeline per process.

    Pass `arbiter=` to control the wiring; otherwise one is built from `settings` via
    `build_default_arbiter`. Either way there *is* an arbiter, unless the caller states
    `allow_unreconciled=True` — a pipeline that quietly resolves nothing is the failure
    mode ADR-010 exists to prevent.
    """
    global _PIPELINE
    if _PIPELINE is None:
        if arbiter is None and not allow_unreconciled:
            arbiter = build_default_arbiter(settings)
        _PIPELINE = IngestPipeline(
            grobid=grobid or GrobidClient(),
            repair_threshold=settings.repair_confidence_threshold,
            styles_dir=settings.csl_styles_dir,
            ambiguity_margin=settings.style_ambiguity_margin,
            arbiter=arbiter,
            segmenter=segmenter,
            store_factory=store_factory,
            allow_unreconciled=allow_unreconciled,
        )
    return _PIPELINE


def reset_ingest_pipeline() -> None:
    """Drop the cached pipeline. Tests only."""
    global _PIPELINE
    _PIPELINE = None


_PIPELINE: IngestPipeline | None = None
