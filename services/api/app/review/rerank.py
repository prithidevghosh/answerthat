"""Rerank candidates against **the claim**, not the topic (ADR-005).

Fusion ranks by how often and how highly the three strategies surfaced a work. That is
a topical signal: a paper can be the best-known work in the area and still say nothing
about the specific assertion the author made. Reranking asks the only question that
matters for a finding — *would this paper bear on this sentence?* — and it asks it with
the claim in front of it, not a query derived from it.

This is not verification. Reranking is cheap triage that decides which handful of
candidates are worth spending an abstract fetch and a verifier call on; the verifier
then has to produce a verbatim quote or the finding dies (ADR-006). Scores here never
appear in a finding on their own.

One call per claim, all candidates in it — per-candidate calls would multiply latency
for a judgement the model makes better with the alternatives side by side. Scores for
`source_id`s that were not in the input are discarded: the model ranks what it was
given and cannot introduce a source.
"""

from __future__ import annotations

from app.core.contracts import Candidate, Claim, SourceRecord
from app.review.llm import StructuredLLM, object_schema

__all__ = ["Reranker", "RERANK_SYSTEM_PROMPT"]

RERANK_SYSTEM_PROMPT = """\
You are triaging retrieved papers for an academic peer-review system.

You are given one claim from a manuscript and a numbered list of candidate papers. For \
each candidate, score how directly it bears on *this specific claim* — not how relevant \
it is to the general topic, and not how important the paper is in its field.

  1.0  Directly addresses this claim: reports, tests, supports, or contradicts it.
  0.7  Addresses a close variant of the claim, or reports the same finding in an
       adjacent setting.
  0.4  Same subject area and would plausibly be cited nearby, but does not speak to
       this claim.
  0.1  Topically related only. A reader would not expect this citation here.
  0.0  Unrelated, or you cannot tell from the metadata given.

A well-known paper that does not address the claim scores low. A minor paper that tests \
exactly this claim scores high. Score only the candidates listed; do not add any.

`why` is one short sentence naming what in the candidate connects it to the claim, or \
what is missing. It is triage reasoning, not evidence — the verification step will \
require a verbatim quote before anything is shown to the researcher.
"""

RERANK_SCHEMA = object_schema(
    {
        "scores": {
            "type": "array",
            "items": object_schema(
                {
                    "source_id": {"type": "string"},
                    "relevance": {"type": "number", "description": "0.0 to 1.0"},
                    "why": {"type": "string"},
                }
            ),
        }
    }
)

#: How much of an abstract the reranker sees. Enough to judge relevance; the verifier
#: reads the whole thing.
_ABSTRACT_PREVIEW = 900


class Reranker:
    def __init__(self, *, llm: StructuredLLM, keep_top: int = 5) -> None:
        self.llm = llm
        # Only this many candidates per claim go on to verification. Verification costs
        # an abstract fetch (rate-limited) plus a model call, so the cap is a real
        # budget decision — and it is reported in the progress counters rather than
        # hidden, so "we checked the top N" is visible rather than implied.
        self.keep_top = keep_top
        self.discarded_unknown_source = 0

    async def rerank(
        self,
        claim: Claim,
        candidates: list[Candidate],
        records: dict[str, SourceRecord],
        *,
        snippets: dict[str, str] | None = None,
    ) -> list[Candidate]:
        """Score candidates against the claim and return the top `keep_top`.

        Candidates the model did not score keep their fused rank and fall below the
        scored ones — an unscored candidate is unjudged, not judged irrelevant.
        """
        if not candidates:
            return []

        prompt = _build_prompt(claim, candidates, records, snippets or {})
        payload = await self.llm.complete_json(
            system=RERANK_SYSTEM_PROMPT,
            prompt=prompt,
            schema=RERANK_SCHEMA,
            max_tokens=8000,
        )

        allowed = {candidate.source_id for candidate in candidates}
        scored: dict[str, float] = {}
        for row in payload.get("scores") or []:
            source_id = str(row.get("source_id", ""))
            if source_id not in allowed:
                # The model named something it was not given. Dropped rather than
                # looked up — a source_id we did not supply is not a source we
                # retrieved (HR-1).
                self.discarded_unknown_source += 1
                continue
            scored[source_id] = min(1.0, max(0.0, float(row.get("relevance") or 0.0)))

        reranked = [
            candidate.model_copy(update={"rerank_score": scored.get(candidate.source_id)})
            for candidate in candidates
        ]
        reranked.sort(
            key=lambda c: (
                # Unscored candidates sort below every scored one.
                c.rerank_score if c.rerank_score is not None else -1.0,
                c.fused_score,
            ),
            reverse=True,
        )
        return reranked[: self.keep_top]

    def snapshot(self) -> dict[str, int]:
        return {"discarded_unknown_source": self.discarded_unknown_source}


def _build_prompt(
    claim: Claim,
    candidates: list[Candidate],
    records: dict[str, SourceRecord],
    snippets: dict[str, str],
) -> str:
    lines = ["CLAIM:", claim.text, "", "CANDIDATES:", ""]
    for candidate in candidates:
        record = records.get(candidate.source_id)
        if record is None:
            continue
        csl = record.csl
        issued = ((csl.get("issued") or {}).get("date-parts") or [[None]])[0]
        year = issued[0] if issued else None
        authors = ", ".join(
            a.get("family") or a.get("literal", "") for a in (csl.get("author") or [])[:3]
        )
        lines.append(f"source_id: {candidate.source_id}")
        lines.append(f"  title: {csl.get('title', '(untitled)')}")
        lines.append(f"  authors: {authors or '(unknown)'}   year: {year or '(unknown)'}")
        lines.append(f"  venue: {csl.get('container-title') or '(unknown)'}")
        matched = snippets.get(candidate.source_id)
        if matched:
            lines.append(f"  matched passage: {matched[:400]}")
        if record.abstract:
            lines.append(f"  abstract: {record.abstract[:_ABSTRACT_PREVIEW]}")
        else:
            lines.append("  abstract: (not retrieved yet)")
        lines.append("")
    return "\n".join(lines)
