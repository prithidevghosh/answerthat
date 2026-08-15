"""Candidate generation — three strategies, in parallel, per claim (ADR-005).

    1. S2 /snippet/search          passage-level evidence, not just titles
    2. S2 Recommendations          seeded with the paper's OWN cited works (SPECTER2)
    3. OpenAlex search + one-hop   cited_by / references expansion from the bibliography

Why three, and why these three: keyword search over section text returns topically
adjacent papers rather than the work the author should have cited, and the brief calls
that failure out by name. Each strategy here contributes something the others cannot.

* `/snippet/search` returns *the sentence that matched*, so a candidate arrives with
  evidence attached rather than a title we then have to justify.
* Recommendations seeded with the bibliography is the only genuinely semantic signal
  either provider gives us — it is a direct expression of "what did this literature
  neighbourhood contain that they missed?", asked in SPECTER2 space rather than in
  words. It is claim-independent, so it is computed **once per document** and reused
  across every claim; recomputing it per claim would spend the S2 rate limit on an
  identical answer.
* The one-hop graph expansion catches the foundational and follow-up work that sits one
  edge away from what the authors already read.

The three run concurrently per claim. They share one rate limiter, so concurrency buys
overlap of the OpenAlex and S2 budgets rather than more S2 requests per second.

Failures propagate. A strategy that raised is not a strategy that found nothing, and
`asyncio.gather` here is deliberately **not** `return_exceptions=True`: two working
strategies plus one throttled one would otherwise produce a shorter candidate list that
reads exactly like a thorough search of a thin literature.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.contracts import Claim, SourceRecord
from app.providers.identity import extract_identifiers
from app.review.fusion import StrategyRanking, cited_keys_for, fuse_candidates

__all__ = ["CandidateGenerator", "DocumentContext"]


class DocumentContext:
    """The per-document work that every claim's search reuses.

    Built once per review. Holds the bibliography's records, the S2 ids used to seed
    Recommendations, the OpenAlex ids used to seed graph expansion, and the dedupe keys
    of everything already cited.
    """

    __slots__ = ("cited_records", "s2_seed_ids", "openalex_seed_ids", "cited_keys", "recommendations")

    def __init__(self, cited_records: list[SourceRecord]) -> None:
        self.cited_records = cited_records
        self.cited_keys = cited_keys_for(cited_records)
        self.s2_seed_ids: list[str] = []
        self.openalex_seed_ids: list[str] = []
        for record in cited_records:
            ids = extract_identifiers(record.csl)
            if ids.s2_paper_id:
                self.s2_seed_ids.append(ids.s2_paper_id)
            if ids.openalex_id:
                self.openalex_seed_ids.append(ids.openalex_id)
        #: Filled lazily by `CandidateGenerator.prepare` — claim-independent, so it is
        #: computed once and reused for every claim in the paper.
        self.recommendations: list[SourceRecord] = []

    @property
    def has_bibliography(self) -> bool:
        return bool(self.s2_seed_ids or self.openalex_seed_ids)


class CandidateGenerator:
    """Runs the three strategies and fuses their results for one claim."""

    def __init__(
        self,
        *,
        semantic_scholar: Any,
        openalex: Any,
        per_strategy_limit: int = 10,
        fused_limit: int = 12,
    ) -> None:
        self.s2 = semantic_scholar
        self.openalex = openalex
        self.per_strategy_limit = per_strategy_limit
        self.fused_limit = fused_limit
        self.snippets_by_source: dict[str, str] = {}

    async def prepare(self, context: DocumentContext) -> None:
        """Compute the document-level Recommendations seed set.

        Called once before the per-claim loop. If the paper's bibliography resolved to
        no S2 ids at all, this strategy legitimately contributes nothing — recorded as
        an empty list rather than an error, because "we could not seed the SPECTER2
        neighbourhood" is a real property of the paper, not a failure of ours.
        """
        if not context.s2_seed_ids:
            return
        context.recommendations = await self.s2.recommendations_from(
            context.s2_seed_ids, limit=50
        )

    async def generate(self, claim: Claim, context: DocumentContext):
        """Return fused, deduped, already-cited-subtracted candidates for one claim."""
        snippets, openalex_hits, graph_hits = await asyncio.gather(
            self._snippets(claim),
            self._openalex_search(claim),
            self._graph_expansion(context),
        )

        rankings = [
            StrategyRanking("s2_snippet", snippets),
            StrategyRanking("s2_recommendations", context.recommendations[: self.per_strategy_limit * 2]),
            StrategyRanking("openalex_search", openalex_hits),
            StrategyRanking("openalex_graph", graph_hits),
        ]
        return fuse_candidates(
            rankings, already_cited=context.cited_keys, limit=self.fused_limit
        )

    # ------------------------------------------------------------------ strategies

    async def _snippets(self, claim: Claim) -> list[SourceRecord]:
        """Strategy 1 — passage-level search on the claim text itself.

        The retrieved snippet text is kept alongside the record: it is real retrieved
        text and it is what makes a finding show *why* a paper is relevant rather than
        asserting that it is.
        """
        hits = await self.s2.snippet_search(claim.text, limit=self.per_strategy_limit)
        records: list[SourceRecord] = []
        for snippet in hits:
            if snippet.record is None:
                continue
            self.snippets_by_source.setdefault(snippet.source_id, snippet.text)
            records.append(snippet.record)
        return records

    async def _openalex_search(self, claim: Claim) -> list[SourceRecord]:
        """Strategy 3a — OpenAlex relevance search against the claim."""
        return await self.openalex.search_works(claim.text, limit=self.per_strategy_limit)

    async def _graph_expansion(self, context: DocumentContext) -> list[SourceRecord]:
        """Strategy 3b — one hop out from the existing bibliography, both directions.

        Claim-independent like Recommendations, but cheap enough to leave here: OpenAlex
        caches it after the first claim, so every later claim is a cache hit rather than
        a re-spent credit.
        """
        if not context.openalex_seed_ids:
            return []
        return await self.openalex.one_hop_expansion(
            context.openalex_seed_ids[:50], limit=self.per_strategy_limit * 2
        )

    def snippet_for(self, source_id: str) -> str | None:
        """The retrieved passage that surfaced this source, if a snippet did."""
        return self.snippets_by_source.get(source_id)
