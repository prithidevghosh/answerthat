"""The rerank cascade and the prompt files. ADR-015 / ADR-016 / ADR-019 / ADR-024.

Two things are under test here, and they are two halves of one argument.

The **cascade** is the reason verification can run on the strongest model we have. Free
embeddings cut to `RERANK_KEEP`, a mini model cuts to `VERIFY_KEEP`, and only those reach
the verifier. If the prefilter silently stopped narrowing, nothing would fail — the review
would still be correct, just proportionally more expensive per claim, which is exactly the
pressure that eventually gets answered by downgrading the verifier. So the narrowing is
asserted rather than assumed.

The **prompts** are files (ADR-019). A prompt change is a behaviour change and should read
like one in the history.
"""

from __future__ import annotations

import pytest

from app.core.contracts import AbstractSource, Candidate, Claim, Provenance, SourceRecord
from app.review.prefilter import EmbeddingPrefilter, cosine

CLAIM = Claim(
    claim_id="clm_1",
    text="Attention mechanisms outperform recurrence on long sequences.",
    span_id="spn_1",
    citability=0.9,
)


class _FakeEmbedder:
    """Returns a scripted vector per text, in order. Positional, like the real one."""

    def __init__(self, vectors: list[list[float]] | None = None) -> None:
        self.vectors = vectors
        self.batches: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        if self.vectors is not None:
            return self.vectors
        return [[1.0, 0.0] for _ in texts]


def _record(source_id: str, title: str, abstract: str | None = None) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        csl={"title": title, "type": "article-journal", "DOI": f"10.1/{source_id}"},
        provenance=Provenance(
            provider="semantic_scholar",
            endpoint="/paper/search",
            retrieved_at="2026-08-16T10:00:00+00:00",
            external_url=f"https://doi.org/10.1/{source_id}",
        ),
        abstract=abstract,
        abstract_source=AbstractSource.S2 if abstract else AbstractSource.UNAVAILABLE,
    )


def _candidates(n: int) -> tuple[list[Candidate], dict[str, SourceRecord]]:
    candidates = [
        Candidate(source_id=f"src_{i}", strategy="s2_snippet", fused_score=1.0 - i / 100)
        for i in range(n)
    ]
    records = {c.source_id: _record(c.source_id, f"Paper {i}", "An abstract.") for i, c in enumerate(candidates)}
    return candidates, records


# --------------------------------------------------------------------------- cascade


async def test_the_prefilter_cuts_the_field_to_rerank_keep() -> None:
    """The narrowing that makes an expensive verifier affordable (ADR-015)."""
    candidates, records = _candidates(12)
    prefilter = EmbeddingPrefilter(llm=_FakeEmbedder(), keep=10)

    kept = await prefilter.select(CLAIM, candidates, records)

    assert len(kept) == 10


async def test_the_prefilter_costs_no_model_call_when_nothing_needs_cutting() -> None:
    """A review small enough not to need narrowing should not pay for a round trip."""
    candidates, records = _candidates(3)
    embedder = _FakeEmbedder()

    kept = await EmbeddingPrefilter(llm=embedder, keep=10).select(CLAIM, candidates, records)

    assert kept == candidates
    assert embedder.batches == []


async def test_the_claim_is_embedded_alongside_the_candidates() -> None:
    """One batch, claim first — the ordering the positional match depends on."""
    candidates, records = _candidates(12)
    embedder = _FakeEmbedder()

    await EmbeddingPrefilter(llm=embedder, keep=5).select(CLAIM, candidates, records)

    assert len(embedder.batches) == 1
    assert embedder.batches[0][0] == CLAIM.text
    assert len(embedder.batches[0]) == 13


async def test_candidates_closest_to_the_claim_survive() -> None:
    """Ranking, not judgement: the prefilter orders, the reranker and verifier decide."""
    candidates, records = _candidates(4)
    # src_2 points exactly where the claim does; the rest point away from it.
    vectors = [[1.0, 0.0], [0.0, 1.0], [0.0, 1.0], [1.0, 0.0], [0.0, 1.0]]

    kept = await EmbeddingPrefilter(llm=_FakeEmbedder(vectors), keep=2).select(
        CLAIM, candidates, records
    )

    assert kept[0].source_id == "src_2"


async def test_a_short_embedding_response_raises_rather_than_dropping_the_tail() -> None:
    """HR-3. A quietly shortened candidate list reads as a thin literature."""
    candidates, records = _candidates(12)
    embedder = _FakeEmbedder([[1.0, 0.0]] * 5)  # asked for 13, answered 5

    with pytest.raises(RuntimeError) as exc:
        await EmbeddingPrefilter(llm=embedder, keep=10).select(CLAIM, candidates, records)
    assert "by position" in str(exc.value)


async def test_a_candidate_with_no_embeddable_text_is_unjudged_not_rejected() -> None:
    """Same rule the reranker applies to a candidate the model did not score."""
    candidates, records = _candidates(12)
    # Two records we hold nothing but an id for: no title, no abstract, nothing to embed.
    for source_id in ("src_0", "src_1"):
        records[source_id] = SourceRecord(
            source_id=source_id,
            csl={"type": "article-journal"},
            provenance=records[source_id].provenance,
        )
    prefilter = EmbeddingPrefilter(llm=_FakeEmbedder(), keep=11)

    kept = await prefilter.select(CLAIM, candidates, records)

    assert prefilter.snapshot()["skipped_no_text"] == 2
    # Still in the running, but behind everything that could actually be scored — the
    # ten embeddable candidates take the first ten slots and one unscorable one fills
    # the last, rather than being dropped outright for having thin metadata.
    assert len(kept) == 11
    assert kept[-1].source_id in {"src_0", "src_1"}
    assert all(c.source_id not in {"src_0", "src_1"} for c in kept[:10])


def test_cosine_refuses_to_compare_vectors_of_different_widths() -> None:
    """A width mismatch means the model or the dimensions setting moved under a cache."""
    with pytest.raises(ValueError):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])


def test_cosine_of_a_zero_vector_is_zero_not_an_exception() -> None:
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_thresholds_come_from_config_not_from_literals() -> None:
    """ADR-024. A threshold that cannot be named cannot be swept against the golden set."""
    from app.core.config import get_settings
    from app.review.claims import ClaimExtractor
    from app.review.rerank import Reranker

    settings = get_settings()
    assert EmbeddingPrefilter(llm=_FakeEmbedder()).keep == settings.rerank_keep
    assert Reranker(llm=_FakeEmbedder()).keep_top == settings.verify_keep
    assert ClaimExtractor(llm=_FakeEmbedder()).min_citability == settings.citability_min


# --------------------------------------------------------------------------- ADR-019


def test_the_three_prompts_are_files_on_disk() -> None:
    import pathlib

    prompts = pathlib.Path(__file__).resolve().parents[3] / "app" / "review" / "prompts"
    names = {path.name for path in prompts.glob("*.md")}
    assert names == {"claim_extraction.md", "rerank.md", "verify.md"}


def test_a_missing_prompt_raises_rather_than_defaulting_to_none() -> None:
    """A model with no instructions still answers, and answers plausibly (HR-3)."""
    from app.review.prompts import load

    with pytest.raises(FileNotFoundError):
        load("does_not_exist.md")


def test_no_prompt_text_is_inlined_in_a_review_module() -> None:
    """ADR-019: prompts are diffable files, not string literals three levels into a class."""
    import pathlib

    review_dir = pathlib.Path(__file__).resolve().parents[3] / "app" / "review"
    offenders = [
        path.name
        for path in review_dir.glob("*.py")
        if "SYSTEM_PROMPT = \"\"\"" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_the_verifier_prompt_states_the_mechanical_check() -> None:
    """The prompt is not the guarantee, but it must not misdescribe it either."""
    from app.review.prompts import VERIFIER_SYSTEM

    assert "verbatim" in VERIFIER_SYSTEM
    assert "checked mechanically" in VERIFIER_SYSTEM
    assert "does_not_address" in VERIFIER_SYSTEM
