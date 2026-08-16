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

**Unavailable is not failed.** `/snippet/search` needs an S2 key: without one it answers
429 to a single cold request, so strategy 1 is not a strategy that might fail, it is one
that cannot run. Those are different facts and they get different handling. A strategy
that *raises* still takes the review with it, unchanged. A strategy that is *unavailable*
is left out of the set before the run, and `strategies` says so — which is why an
unauthenticated review now returns candidates from the other three rather than dying at
the first claim. The line to hold is that neither case ever becomes a silent `[]`: the
first raises, the second is declared.
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

    #: Every strategy ADR-005 defines, in fusion order. `strategies_for` returns the
    #: subset that can actually run; the difference is what the reader is owed.
    ALL_STRATEGIES = ("s2_snippet", "s2_recommendations", "openalex_search", "openalex_graph")

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
        # Read once, strictly, at construction. Strictly because a provider that cannot
        # say whether its search pool works is a wiring bug and should say so here rather
        # than resolve to a plausible default; once because the answer follows from
        # whether a key exists, which does not change while the process runs.
        self.snippet_search_available: bool = bool(semantic_scholar.search_pool_available)

    def strategies_for(self, context: DocumentContext) -> tuple[str, ...]:
        """The strategies that will actually run for this document.

        Two independent reductions, and the caller cannot tell them apart from the
        result alone — hence this. Without a bibliography the two seeded strategies have
        nothing to seed from; without an S2 key `s2_snippet` has no endpoint. Both are
        legitimate, both narrow the search, and neither should be inferred from a
        shorter findings list.
        """
        available = []
        for strategy in self.ALL_STRATEGIES:
            if strategy == "s2_snippet" and not self.snippet_search_available:
                continue
            if strategy == "s2_recommendations" and not context.s2_seed_ids:
                continue
            if strategy == "openalex_graph" and not context.openalex_seed_ids:
                continue
            available.append(strategy)
        return tuple(available)

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
        # `_snippets` is omitted from the gather rather than called and forgiven. There
        # is no `return_exceptions=True` here on purpose, so calling an endpoint we know
        # is closed would still abort every claim — the bug this replaces.
        snippet_task = self._snippets(claim) if self.snippet_search_available else _none()
        snippets, openalex_hits, graph_hits = await asyncio.gather(
            snippet_task,
            self._openalex_search(claim),
            self._graph_expansion(context),
        )

        # An unavailable strategy contributes no ranking at all. Note this is not the
        # same as contributing an empty one: reciprocal-rank fusion over an empty list is
        # harmless, but the *record* of which strategies ran is what `strategies_for`
        # reports, and a phantom `s2_snippet` in it would be a lie about coverage.
        rankings = [
            StrategyRanking("s2_recommendations", context.recommendations[: self.per_strategy_limit * 2]),
            StrategyRanking("openalex_search", openalex_hits),
            StrategyRanking("openalex_graph", graph_hits),
        ]
        if snippets is not None:
            rankings.insert(0, StrategyRanking("s2_snippet", snippets))
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
        """The retrieved passage that surfaced this source, if a snippet did.

        `None` whenever `s2_snippet` did not run, which is every source in an
        unauthenticated review. Findings still carry their evidence — ADR-006's verbatim
        quote comes from the fetched abstract, not from here — so what is lost is the
        matched passage shown alongside a finding, not the basis for making it.
        """
        return self.snippets_by_source.get(source_id)


async def _none() -> None:
    """A placeholder coroutine for a strategy that is not running.

    `asyncio.gather` needs an awaitable per slot, and `None` is deliberately not `[]`:
    it keeps "this strategy did not run" distinguishable from "this strategy ran and
    found nothing" all the way to the fusion step.
    """
    return None
