"""Reciprocal-rank fusion, cross-provider dedupe, and subtraction of what's already cited.

Pure functions over records the adapters already stored — no network, no model, no
`source_store` writes. That makes the ranking logic unit-testable without fixtures and
keeps the one module that decides *what the researcher sees* free of I/O.

Three steps, in this order, and the order matters:

1. **Fuse** the three strategies by reciprocal rank. RRF is used rather than score
   averaging because the strategies' scores are not comparable — S2's snippet relevance,
   SPECTER2 recommendation order, and OpenAlex's relevance are three different scales.
   Rank position is the only thing they share.
2. **Dedupe** across providers. The same paper arriving from S2 and OpenAlex must
   collapse to one candidate or fusion double-counts it and it outranks everything.
3. **Subtract** everything the paper already cites — last, so a work found by all three
   strategies is removed once rather than surviving in a strategy the subtraction
   missed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.contracts import Candidate, SourceRecord
from app.providers.identity import dedupe_key, extract_identifiers

__all__ = ["StrategyRanking", "fuse_candidates", "RRF_K", "cited_keys_for"]

#: The standard reciprocal-rank-fusion constant. Damps the top of each list so a single
#: strategy's first result cannot dominate a work that all three ranked highly.
RRF_K = 60


@dataclass
class StrategyRanking:
    """One strategy's results, in rank order (best first)."""

    strategy: str
    records: list[SourceRecord] = field(default_factory=list)


def cited_keys_for(records: list[SourceRecord]) -> set[str]:
    """Dedupe keys for the works a paper already cites.

    Built from the same function fusion collapses on, so "already cited" and "the same
    paper" mean exactly one thing. A DOI match and a normalized-title match both count.
    """
    return {dedupe_key(extract_identifiers(record.csl)) for record in records}


def fuse_candidates(
    rankings: list[StrategyRanking],
    *,
    already_cited: set[str] | None = None,
    limit: int = 25,
) -> list[Candidate]:
    """Fuse, dedupe, subtract, and return candidates ordered by fused score.

    A record contributed by several strategies keeps the strategy that ranked it
    highest, so the `Candidate.strategy` field answers "where did this come from?" with
    the most informative answer rather than an arbitrary one.
    """
    cited = already_cited or set()

    scores: dict[str, float] = {}
    best_strategy: dict[str, tuple[int, str]] = {}
    representative: dict[str, SourceRecord] = {}

    for ranking in rankings:
        for position, record in enumerate(ranking.records):
            key = dedupe_key(extract_identifiers(record.csl))
            if key in cited:
                # Already in the bibliography. Not a finding — the author has it.
                continue
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + position + 1)
            # Keep the record from whichever strategy ranked it best; ties keep the
            # first strategy seen, which makes the output order-stable.
            if key not in best_strategy or position < best_strategy[key][0]:
                best_strategy[key] = (position, ranking.strategy)
                representative[key] = record

    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    candidates: list[Candidate] = []
    for key, score in ordered[:limit]:
        record = representative[key]
        candidates.append(
            Candidate(
                source_id=record.source_id,
                strategy=best_strategy[key][1],  # type: ignore[arg-type]
                fused_score=round(score, 6),
            )
        )
    return candidates
