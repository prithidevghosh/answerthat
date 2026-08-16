"""The embedding prefilter — the first stage of the rerank cascade. ADR-015 / ADR-016.

Rerank is the highest-volume model call in the system: claims × candidates. The cost
control for that is **not** a cheaper verifier — it is this cascade:

    fuse (≈12 candidates)  ──embed, free──►  RERANK_KEEP
                           ──role RERANK──►  VERIFY_KEEP
                           ──role VERIFY──►  a finding, or nothing

Each stage is cheaper per candidate than the one after it and cuts the field for it. That
ordering is what lets `VERIFY` run on the strongest model we have without the bill scaling
with the number of retrieved papers — and verification is the judgment the product's
honesty rests on, so it is the stage that must not be economised (ADR-015).

**This stage ranks, it never judges.** Cosine similarity between a claim and a title-plus-
abstract is a topical signal, and topical adjacency is precisely what ADR-005 says is not
a finding. Nothing here decides that a paper is relevant; it decides which papers are
worth spending a model call on. The claim-versus-topic judgement is the reranker's, and
the evidence requirement is the verifier's.

**A short embedding response raises.** Vectors are matched to their candidates by
position, so a response that came back short cannot be aligned — and silently dropping the
tail would quietly shrink the candidate set, which reads downstream as a thin literature
rather than as the failure it is (HR-3).
"""

from __future__ import annotations

import math
from typing import Any

from app.core.contracts import Candidate, Claim, SourceRecord

__all__ = ["EmbeddingPrefilter", "cosine"]

#: How much of an abstract goes into a candidate's embedding text. Enough to place the
#: paper topically; the verifier reads the whole thing.
_ABSTRACT_CHARS = 1200


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Zero for a zero-magnitude vector rather than a ZeroDivisionError."""
    if len(a) != len(b):
        raise ValueError(
            f"cannot compare a {len(a)}-dimension vector with a {len(b)}-dimension one; "
            "an embedding of the wrong width means the model or the dimensions setting "
            "changed under a cached vector."
        )
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingPrefilter:
    """Cuts fused candidates to `RERANK_KEEP` before any model call is made."""

    def __init__(self, *, llm: Any, keep: int | None = None) -> None:
        self.llm = llm
        # RERANK_KEEP (ADR-024). Named rather than inlined so T1 can sweep it against
        # the golden set — the question it answers is "how wide a net does the reranker
        # need to see to not miss the right paper?", which is empirical.
        if keep is None:
            from app.core.config import get_settings  # noqa: PLC0415

            keep = get_settings().rerank_keep
        self.keep = keep
        self.embedded = 0
        self.skipped_no_text = 0

    async def select(
        self,
        claim: Claim,
        candidates: list[Candidate],
        records: dict[str, SourceRecord],
        *,
        snippets: dict[str, str] | None = None,
    ) -> list[Candidate]:
        """Return the `keep` candidates closest to the claim in embedding space.

        Candidates with no embeddable text keep their fused rank and fill any remaining
        slots **below** the scored ones. They are unjudged, not judged irrelevant — the
        same rule the reranker applies to a candidate the model did not score.
        """
        if len(candidates) <= self.keep:
            # Nothing to cut. Skipping the call keeps a small review free of a network
            # round trip whose only effect would be to reorder a list the reranker sees
            # in full anyway.
            return candidates

        texts: list[str] = []
        embeddable: list[Candidate] = []
        unembeddable: list[Candidate] = []
        for candidate in candidates:
            text = _embedding_text(records.get(candidate.source_id), (snippets or {}).get(candidate.source_id))
            if text:
                texts.append(text)
                embeddable.append(candidate)
            else:
                self.skipped_no_text += 1
                unembeddable.append(candidate)

        if not embeddable:
            return candidates[: self.keep]

        vectors = await self.llm.embed([claim.text, *texts])
        if len(vectors) != len(texts) + 1:
            raise RuntimeError(
                f"asked for {len(texts) + 1} embeddings and got {len(vectors)}. Vectors are "
                "matched to candidates by position, so a short response cannot be aligned; "
                "dropping the tail would shrink the candidate set silently (HR-3)."
            )
        self.embedded += len(texts)

        claim_vector, candidate_vectors = vectors[0], vectors[1:]
        scored = sorted(
            zip(embeddable, candidate_vectors, strict=True),
            key=lambda pair: (-cosine(claim_vector, pair[1]), -pair[0].fused_score),
        )
        ordered = [candidate for candidate, _ in scored] + unembeddable
        return ordered[: self.keep]

    def snapshot(self) -> dict[str, int]:
        return {"embedded": self.embedded, "skipped_no_text": self.skipped_no_text}


def _embedding_text(record: SourceRecord | None, snippet: str | None) -> str:
    """What a candidate looks like to the embedder: title, matched passage, abstract.

    The matched passage comes first among the evidence because it is the sentence
    `/snippet/search` actually matched — real retrieved text, and the most specific thing
    we hold about why this paper surfaced at all.
    """
    if record is None:
        return ""
    parts: list[str] = []
    title = record.csl.get("title")
    if isinstance(title, str) and title.strip():
        parts.append(title.strip())
    if snippet and snippet.strip():
        parts.append(snippet.strip()[:_ABSTRACT_CHARS])
    if record.abstract and record.abstract.strip():
        parts.append(record.abstract.strip()[:_ABSTRACT_CHARS])
    return "\n".join(parts)
