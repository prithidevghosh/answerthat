"""The streaming review runner. ADR-014.

Claim-first review at ~1 request/second on Semantic Scholar means a 40-claim paper takes
five to eight minutes. That floor is set by the rate limit, not by our code, so the
design question is not "how do we make it fast" but "what does the researcher see during
those minutes". Nobody watches a spinner for six minutes; everybody watches findings
appear.

So: findings stream as each one verifies, **ordered by citability descending**, and the
first thirty seconds carry the most consequential findings in the paper. Progress is
reported as `verified / total` throughout, which is what stops a partially-complete
review from being mistaken for a complete one — the dishonesty HR-3 exists to prevent.

Claims are processed strictly in citability order rather than concurrently. Both providers
share one rate limiter, so out-of-order concurrency would buy little throughput and would
cost the ordering guarantee; the parallelism that pays is *within* a claim — three
retrieval strategies at once, then the surviving candidates verified at once.

Three honesty surfaces are wired in here rather than left to the UI:

* Zero candidates after subtraction is a **finding** (`no_candidates_found`), not silence.
* `unverifiable_no_abstract` is emitted and displayed, not skipped.
* A quote-check kill is counted in progress, so a short feed is explained rather than
  ambiguous.

B3 owns the SSE endpoint; this module is the async generator behind it, yielding
`(event_name, payload)` per the `ReviewRunner` port.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Any, Literal

from app.core.contracts import (
    Claim,
    Document,
    Finding,
    SourceRecord,
    VerificationLabel,
)
from app.review.candidates import CandidateGenerator, DocumentContext
from app.review.claims import ClaimExtractor
from app.review.prefilter import EmbeddingPrefilter
from app.review.rerank import Reranker
from app.review.verifier import Verifier

__all__ = ["ReviewRunner", "ReviewStats"]

#: Verdicts worth showing as missing work. `does_not_address` is counted, not emitted —
#: it is a negative result about a candidate, not a finding about the paper.
Severity = Literal["high", "medium", "low", "info"]

_MISSING_WORK_SEVERITY: dict[VerificationLabel, Severity] = {
    VerificationLabel.SUPPORTS: "high",
    VerificationLabel.CONTRADICTS: "high",
    VerificationLabel.PARTIALLY_SUPPORTS: "medium",
    VerificationLabel.UNVERIFIABLE_NO_ABSTRACT: "info",
}

#: A cited source that does not support the claim it is attached to is the more serious
#: finding of the two kinds — it is a defect in the manuscript as written.
_MISMATCH_SEVERITY: dict[VerificationLabel, Severity] = {
    VerificationLabel.CONTRADICTS: "high",
    VerificationLabel.DOES_NOT_ADDRESS: "high",
    VerificationLabel.PARTIALLY_SUPPORTS: "medium",
    VerificationLabel.UNVERIFIABLE_NO_ABSTRACT: "info",
}


class ReviewStats:
    """Counters surfaced with every progress event.

    Every number here exists so a short feed is explicable. "We found four findings" and
    "we found four findings, discarded eleven candidates on the quote check, and could
    not retrieve six abstracts" are very different reports of the same run.
    """

    __slots__ = (
        "claims_total", "claims_verified", "findings_emitted",
        "candidates_prefiltered", "candidates_considered", "candidates_rejected",
        "quote_check_failures", "unverifiable_no_abstract", "claims_without_candidates",
    )

    def __init__(self) -> None:
        self.claims_total = 0
        self.claims_verified = 0
        self.findings_emitted = 0
        # Survived the embedding prefilter; `candidates_considered` is the narrower set
        # that survived the reranker and cost a verifier call.
        self.candidates_prefiltered = 0
        self.candidates_considered = 0
        self.candidates_rejected = 0
        self.quote_check_failures = 0
        self.unverifiable_no_abstract = 0
        self.claims_without_candidates = 0

    def as_dict(self) -> dict[str, int]:
        return {slot: getattr(self, slot) for slot in self.__slots__}


class ReviewRunner:
    """Streams findings for one document, highest citability first."""

    def __init__(
        self,
        *,
        document_store: Any,
        source_store: Any,
        extractor: ClaimExtractor,
        candidates: CandidateGenerator,
        reranker: Reranker,
        verifier: Verifier,
        prefilter: EmbeddingPrefilter | None = None,
        check_existing_anchors: bool = True,
    ) -> None:
        self.documents = document_store
        self.store = source_store
        self.extractor = extractor
        self.candidates = candidates
        # The cascade's first stage (ADR-015). Optional only so a caller that has already
        # narrowed its candidates can skip it; the review pipeline always supplies one.
        self.prefilter = prefilter
        self.reranker = reranker
        self.verifier = verifier
        # The second caller of the one verifier (ADR-006): pointed at existing anchors
        # it asks whether the cited source supports the claim.
        self.check_existing_anchors = check_existing_anchors

    async def stream(
        self, doc_id: str, *, section_ids: list[str] | None = None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield `(event_name, payload)` events for the SSE endpoint B3 owns."""
        stats = ReviewStats()

        document = await self.documents.get(doc_id)
        if document is None:
            raise KeyError(f"document {doc_id!r} not found")

        anchor_sources = _anchor_source_map(document)
        cited_records = await self._load_cited(anchor_sources)
        context = DocumentContext(cited_records)

        claims = await self.extractor.extract(document, section_ids or [])
        stats.claims_total = len(claims)
        yield ("progress", {"phase": "claims_extracted", **stats.as_dict()})

        if not claims:
            # An honest terminal state, not an empty stream: the section had nothing
            # that could be checked against the literature.
            yield ("done", {"reason": "no_citable_claims", **stats.as_dict()})
            return

        # Claim-independent and expensive: the SPECTER2 seed set is computed once.
        await self.candidates.prepare(context)

        for claim in claims:
            async for event in self._review_claim(
                claim, context, anchor_sources, stats, doc_id=doc_id
            ):
                yield event
            stats.claims_verified += 1
            yield ("progress", {"phase": "claim_verified", **stats.as_dict()})

        yield ("done", {"reason": "complete", **stats.as_dict()})

    # ------------------------------------------------------------------ per claim

    async def _review_claim(
        self,
        claim: Claim,
        context: DocumentContext,
        anchor_sources: dict[str, list[str]],
        stats: ReviewStats,
        *,
        doc_id: str = "",
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        if self.check_existing_anchors:
            async for event in self._check_anchors(claim, anchor_sources, stats, doc_id=doc_id):
                yield event

        candidates = await self.candidates.generate(claim, context)
        if not candidates:
            # ADR-006's third honesty surface. Silence here would read as "we looked and
            # said nothing", which is indistinguishable from "we never looked".
            stats.claims_without_candidates += 1
            stats.findings_emitted += 1
            yield ("finding", _finding_payload(Finding(
                finding_id=_finding_id(claim.claim_id, "none"),
                kind="no_candidates_found",
                claim=claim,
                source_id=None,
                verification=None,
                severity="info",
            )))
            return

        records = await self._records_for([c.source_id for c in candidates])

        # The cascade (ADR-015). Embeddings are free and local, so they run first and cut
        # the field to RERANK_KEEP; the mini reranker then cuts to VERIFY_KEEP; only
        # those reach the verifier. Skipping the prefilter would not change what a finding
        # has to prove — it would just make the verifier the volume stage, which is the
        # one place cost pressure turns into a worse verdict.
        if self.prefilter is not None:
            candidates = await self.prefilter.select(
                claim, candidates, records, snippets=self.candidates.snippets_by_source
            )
            stats.candidates_prefiltered += len(candidates)

        shortlist = await self.reranker.rerank(
            claim,
            candidates,
            records,
            snippets=self.candidates.snippets_by_source,
            doc_id=doc_id,
        )
        stats.candidates_considered += len(shortlist)

        # Verified concurrently: each needs an abstract fetch and a model call, and the
        # provider limiter already serialises the part that has to be serialised.
        outcomes = await asyncio.gather(
            *(
                self.verifier.verify_detailed(claim.text, records[c.source_id], doc_id=doc_id)
                for c in shortlist
                if c.source_id in records
            )
        )

        for candidate, outcome in zip(
            [c for c in shortlist if c.source_id in records], outcomes, strict=True
        ):
            if outcome.quote_check_failed:
                stats.quote_check_failures += 1
                continue
            verification = outcome.verification
            if verification is None:
                continue
            if verification.label is VerificationLabel.UNVERIFIABLE_NO_ABSTRACT:
                stats.unverifiable_no_abstract += 1
            severity = _MISSING_WORK_SEVERITY.get(verification.label)
            if severity is None:
                # does_not_address — a candidate that did not pan out, counted so the
                # ratio of searched-to-surfaced is visible.
                stats.candidates_rejected += 1
                continue
            stats.findings_emitted += 1
            yield ("finding", _finding_payload(Finding(
                finding_id=_finding_id(claim.claim_id, candidate.source_id),
                kind="missing_work",
                claim=claim,
                source_id=candidate.source_id,
                verification=verification,
                severity=severity,
            ), extra={
                "strategy": candidate.strategy,
                "fused_score": candidate.fused_score,
                "rerank_score": candidate.rerank_score,
                "matched_passage": self.candidates.snippet_for(candidate.source_id),
                "external_url": records[candidate.source_id].provenance.external_url,
                "csl": records[candidate.source_id].csl,
            }))

    async def _check_anchors(
        self,
        claim: Claim,
        anchor_sources: dict[str, list[str]],
        stats: ReviewStats,
        *,
        doc_id: str = "",
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """The verifier's second caller: does the cited source support this claim?"""
        source_ids = [
            source_id
            for anchor_id in claim.anchor_ids
            for source_id in anchor_sources.get(anchor_id, [])
        ]
        if not source_ids:
            return
        records = await self._records_for(source_ids)
        for source_id in source_ids:
            record = records.get(source_id)
            if record is None:
                continue
            outcome = await self.verifier.verify_detailed(claim.text, record, doc_id=doc_id)
            if outcome.quote_check_failed:
                stats.quote_check_failures += 1
                continue
            verification = outcome.verification
            if verification is None or verification.label is VerificationLabel.SUPPORTS:
                # The citation does what it claims to. Nothing to report.
                continue
            if verification.label is VerificationLabel.UNVERIFIABLE_NO_ABSTRACT:
                stats.unverifiable_no_abstract += 1
            stats.findings_emitted += 1
            yield ("finding", _finding_payload(Finding(
                finding_id=_finding_id(claim.claim_id, source_id, kind="mismatch"),
                kind="claim_citation_mismatch",
                claim=claim,
                source_id=source_id,
                verification=verification,
                severity=_MISMATCH_SEVERITY.get(verification.label, "info"),
            ), extra={
                "external_url": record.provenance.external_url,
                "csl": record.csl,
            }))

    # ------------------------------------------------------------------ store access

    async def _load_cited(self, anchor_sources: dict[str, list[str]]) -> list[SourceRecord]:
        ids = {sid for sids in anchor_sources.values() for sid in sids}
        records = await self._records_for(sorted(ids))
        return list(records.values())

    async def _records_for(self, source_ids: list[str]) -> dict[str, SourceRecord]:
        """Read records from the append-only store.

        Warm first, then read: an unwarmed id raises rather than answering "absent", so
        the review never mistakes a source it failed to load for one that isn't there.
        """
        if not source_ids:
            return {}
        await self.store.warm(source_ids)
        found: dict[str, SourceRecord] = {}
        for source_id in source_ids:
            record = self.store.get(source_id)
            if record is not None:
                found[source_id] = record
        return found


def _anchor_source_map(document: Document) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for section in document.sections:
        for block in section.blocks:
            for span in block.spans:
                for anchor in span.citation_anchors:
                    mapping[anchor.anchor_id] = list(anchor.source_ids)
    return mapping


def _finding_id(claim_id: str, source_id: str, *, kind: str = "missing") -> str:
    digest = hashlib.sha256(f"{kind}:{claim_id}:{source_id}".encode()).hexdigest()[:16]
    return f"fnd_{digest}"


def _finding_payload(
    finding: Finding, *, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = finding.model_dump(mode="json")
    if extra:
        payload.update({k: v for k, v in extra.items() if v is not None})
    return payload
