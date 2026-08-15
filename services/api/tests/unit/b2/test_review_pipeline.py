"""Review pipeline: claim extraction, fusion, rerank, verification, streaming.

The tests that matter here are the mechanical ones — the quote substring check and the
claim-offset check. Those are the two places where a model's output is falsified by code
rather than trusted, and they are what ADR-003 and ADR-006 actually buy.
"""

from __future__ import annotations

import pytest

from app.core.contracts import (
    AbstractSource,
    Block,
    CitationAnchor,
    Claim,
    Document,
    DocumentMeta,
    MissingAPIKeyError,
    Provenance,
    Section,
    SourceRecord,
    Span,
    VerificationLabel,
)
from app.providers.abstracts import AbstractResolver
from app.review.claims import ClaimExtractor
from app.review.fusion import StrategyRanking, cited_keys_for, fuse_candidates
from app.review.llm import DEFAULT_MODEL, StructuredLLM
from app.review.rerank import Reranker
from app.review.stream import ReviewRunner
from app.review.verifier import MIN_QUOTE_CHARS, Verifier, quote_is_present

ABSTRACT = (
    "We propose a new simple network architecture, the Transformer, based solely on "
    "attention mechanisms, dispensing with recurrence and convolutions entirely. "
    "Experiments on two machine translation tasks show these models to be superior in "
    "quality while being more parallelizable and requiring significantly less time to train."
)


# --------------------------------------------------------------------------- fakes


class FakeLLM(StructuredLLM):
    """A StructuredLLM with the wire replaced, not the checks."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.model = "fake"
        self.effort = "high"
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    async def complete_json(self, *, system, prompt, schema, max_tokens=8000):
        self.prompts.append(prompt)
        self.calls += 1
        if not self.responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        return self.responses.pop(0)


class FakeAbstracts(AbstractResolver):
    def __init__(self, text: str | None, source=AbstractSource.S2) -> None:
        self.text = text
        self.source = source
        self.counts = {}

    async def resolve(self, record):
        from app.providers.abstracts import AbstractResult

        if self.text is None:
            return AbstractResult(None, AbstractSource.UNAVAILABLE, (AbstractSource.S2,))
        return AbstractResult(self.text, self.source, (AbstractSource.S2,))


def make_record(source_id: str, title: str, *, doi: str | None = None, abstract=None):
    """A record shaped like one an adapter already stored.

    Built directly rather than through an adapter because these tests exercise the
    review layer's pure logic. Nothing here goes near `source_store.put()` — that path
    has its own guards and its own tests (`test_source_store_hr1.py`).
    """
    csl = {"title": title, "type": "article-journal"}
    if doi:
        csl["DOI"] = doi
    return SourceRecord(
        source_id=source_id,
        csl=csl,
        provenance=Provenance(
            provider="semantic_scholar",
            endpoint="/paper/search",
            retrieved_at="2026-08-15T10:00:00+00:00",
            external_url=f"https://doi.org/{doi}" if doi else "https://example.org/x",
        ),
        abstract=abstract,
        abstract_source=AbstractSource.S2 if abstract else AbstractSource.UNAVAILABLE,
    )


# --------------------------------------------------------------------------- ADR-006


def test_a_verbatim_quote_passes_the_check() -> None:
    assert quote_is_present("dispensing with recurrence and convolutions entirely", ABSTRACT)


def test_a_paraphrase_is_killed() -> None:
    """The whole point. A convincing paraphrase is exactly what a fabrication looks like."""
    assert not quote_is_present(
        "the authors dispense with recurrence and convolution altogether", ABSTRACT
    )


def test_a_fluent_invention_is_killed() -> None:
    assert not quote_is_present(
        "We report a 40% improvement over the previous state of the art.", ABSTRACT
    )


def test_re_encoded_punctuation_still_passes() -> None:
    """Curly quotes and en-dashes are re-encoding, not paraphrase — they must pass."""
    abstract = "The model’s accuracy improved by 3–4 points on the held-out set."
    assert quote_is_present("The model's accuracy improved by 3-4 points", abstract)


def test_reflowed_whitespace_still_passes() -> None:
    assert quote_is_present(
        "dispensing   with\n  recurrence and convolutions entirely", ABSTRACT
    )


def test_a_fragment_too_short_to_falsify_is_rejected() -> None:
    """A three-word "quote" matches almost any abstract; the check must not be gameable."""
    assert len("attention mechanisms") < MIN_QUOTE_CHARS
    assert not quote_is_present("attention mechanisms", ABSTRACT)


def test_stemming_and_stopword_matching_are_deliberately_absent() -> None:
    assert not quote_is_present(
        "propose new simple network architecture Transformer based attention", ABSTRACT
    )


# --------------------------------------------------------------------------- verifier


@pytest.fixture
def record():
    return make_record("src_attn", "Attention Is All You Need")


async def test_verifier_kills_a_finding_whose_quote_is_not_in_the_abstract(record) -> None:
    llm = FakeLLM([{
        "label": "supports",
        "quote": "This paper reports a 40% improvement in translation quality.",
        "confidence": 0.9,
    }])
    verifier = Verifier(llm=llm, abstracts=FakeAbstracts(ABSTRACT), source_store=None)

    outcome = await verifier.verify_detailed("Transformers outperform RNNs.", record)

    assert outcome.verification is None
    assert outcome.quote_check_failed is True
    assert verifier.quote_check_failures == 1
    assert verifier.verified == 0


async def test_verifier_accepts_a_quoted_verdict(record) -> None:
    llm = FakeLLM([{
        "label": "supports",
        "quote": "show these models to be superior in quality while being more parallelizable",
        "confidence": 0.85,
    }])
    verifier = Verifier(llm=llm, abstracts=FakeAbstracts(ABSTRACT), source_store=None)

    outcome = await verifier.verify_detailed("Transformers outperform RNNs.", record)

    assert outcome.verification is not None
    assert outcome.verification.label is VerificationLabel.SUPPORTS
    assert outcome.verification.abstract_source is AbstractSource.S2
    assert verifier.verified == 1


async def test_no_abstract_yields_the_fourth_outcome_without_calling_the_model(record) -> None:
    """`unverifiable_no_abstract` is the only legal output — and it costs no model call."""
    llm = FakeLLM([])  # any call would raise
    verifier = Verifier(llm=llm, abstracts=FakeAbstracts(None), source_store=None)

    outcome = await verifier.verify_detailed("Anything.", record)

    assert outcome.verification is not None
    assert outcome.verification.label is VerificationLabel.UNVERIFIABLE_NO_ABSTRACT
    assert outcome.verification.quote is None
    assert llm.calls == 0


async def test_the_kernel_facing_verify_fails_closed_on_a_quote_check_kill(record) -> None:
    """B3's kernel needs SUPPORTS to accept a change; an unquotable claim must not give it."""

    class _Store:
        async def fetch(self, source_id):
            return record

    llm = FakeLLM([{"label": "supports", "quote": "invented text nobody wrote here", "confidence": 1.0}])
    verifier = Verifier(llm=llm, abstracts=FakeAbstracts(ABSTRACT), source_store=_Store())

    verification = await verifier.verify("Transformers outperform RNNs.", "src_attn")

    assert verification.label is VerificationLabel.DOES_NOT_ADDRESS
    assert verification.quote is None
    assert verification.confidence == 0.0


async def test_verify_refuses_a_source_id_that_is_not_in_the_store() -> None:
    class _EmptyStore:
        async def fetch(self, source_id):
            return None

    verifier = Verifier(
        llm=FakeLLM([]), abstracts=FakeAbstracts(ABSTRACT), source_store=_EmptyStore()
    )
    with pytest.raises(KeyError):
        await verifier.verify("Anything.", "src_fabricated")


def test_the_abstract_is_delimited_in_the_prompt() -> None:
    """The model must be able to tell the abstract from the claim it is judging."""
    from app.review.verifier import _build_prompt

    prompt = _build_prompt("A claim.", make_record("src_x", "T"), ABSTRACT)
    assert "<<<ABSTRACT" in prompt and "ABSTRACT>>>" in prompt
    assert prompt.index("A claim.") < prompt.index("<<<ABSTRACT")


# --------------------------------------------------------------------------- claims


def _doc_with(text: str, anchors: list[CitationAnchor] | None = None) -> Document:
    span = Span(id="spn_1", text=text, citation_anchors=anchors or [])
    return Document(
        doc_id="doc_1",
        version=1,
        metadata=DocumentMeta(title="T"),
        sections=[
            Section(
                id="sec_1",
                level=1,
                title="Introduction",
                order=0,
                blocks=[Block(id="blk_1", type="paragraph", order=0, spans=[span])],
            )
        ],
    )


SPAN_TEXT = (
    "Transformer models dominate sequence modelling. Their quadratic attention cost "
    "has motivated a long line of efficiency work."
)


async def test_claim_text_is_sliced_from_the_paper_not_taken_from_the_model() -> None:
    start, end = 0, 47
    llm = FakeLLM([{"claims": [{
        "span_id": "spn_1",
        "char_start": start,
        "char_end": end,
        "quote": SPAN_TEXT[start:end],
        "citability": 0.9,
    }]}])
    extractor = ClaimExtractor(llm=llm)

    (claim,) = await extractor.extract(_doc_with(SPAN_TEXT), [])

    assert claim.text == SPAN_TEXT[start:end].strip()
    assert claim.text in SPAN_TEXT, "a claim must be a verbatim substring of the paper"


async def test_a_claim_whose_quote_disagrees_with_its_offsets_is_dropped() -> None:
    """Mis-anchored claims would attach findings to the wrong sentence — ADR-003's spirit."""
    llm = FakeLLM([{"claims": [{
        "span_id": "spn_1",
        "char_start": 0,
        "char_end": 47,
        "quote": "Transformers are the best models ever invented.",
        "citability": 0.9,
    }]}])
    extractor = ClaimExtractor(llm=llm)

    assert await extractor.extract(_doc_with(SPAN_TEXT), []) == []
    assert extractor.discarded_offset_mismatch == 1


async def test_anchor_ids_are_computed_from_offsets_not_supplied_by_the_model() -> None:
    anchors = [
        CitationAnchor(anchor_id="anc_in", source_ids=["src_a"], offset_in_span=20),
        CitationAnchor(anchor_id="anc_out", source_ids=["src_b"], offset_in_span=120),
    ]
    llm = FakeLLM([{"claims": [{
        "span_id": "spn_1",
        "char_start": 0,
        "char_end": 47,
        "quote": SPAN_TEXT[0:47],
        "citability": 0.9,
    }]}])

    (claim,) = await ClaimExtractor(llm=llm).extract(_doc_with(SPAN_TEXT, anchors), [])

    assert claim.anchor_ids == ["anc_in"]


async def test_claims_stream_in_descending_citability_order() -> None:
    """ADR-014: the first thirty seconds carry the most consequential findings."""
    llm = FakeLLM([{"claims": [
        {"span_id": "spn_1", "char_start": 48, "char_end": 125,
         "quote": SPAN_TEXT[48:125], "citability": 0.4},
        {"span_id": "spn_1", "char_start": 0, "char_end": 47,
         "quote": SPAN_TEXT[0:47], "citability": 0.95},
    ]}])

    claims = await ClaimExtractor(llm=llm).extract(_doc_with(SPAN_TEXT), [])

    assert [c.citability for c in claims] == [0.95, 0.4]


async def test_uncitable_discourse_is_dropped_below_the_threshold() -> None:
    llm = FakeLLM([{"claims": [{
        "span_id": "spn_1", "char_start": 0, "char_end": 47,
        "quote": SPAN_TEXT[0:47], "citability": 0.0,
    }]}])
    extractor = ClaimExtractor(llm=llm)

    assert await extractor.extract(_doc_with(SPAN_TEXT), []) == []
    assert extractor.discarded_below_threshold == 1


async def test_a_claim_for_an_unknown_span_is_dropped() -> None:
    llm = FakeLLM([{"claims": [{
        "span_id": "spn_nonexistent", "char_start": 0, "char_end": 47,
        "quote": SPAN_TEXT[0:47], "citability": 0.9,
    }]}])
    extractor = ClaimExtractor(llm=llm)
    assert await extractor.extract(_doc_with(SPAN_TEXT), []) == []


# --------------------------------------------------------------------------- fusion


def test_reciprocal_rank_fusion_rewards_agreement_across_strategies() -> None:
    """A work all three strategies ranked highly must beat one strategy's top hit."""
    agreed = make_record("src_agreed", "Agreed", doi="10.1/agreed")
    top_only = make_record("src_top", "Top of one list", doi="10.1/top")
    filler = [make_record(f"src_f{i}", f"F{i}", doi=f"10.1/f{i}") for i in range(3)]

    candidates = fuse_candidates([
        StrategyRanking("s2_snippet", [top_only, agreed]),
        StrategyRanking("s2_recommendations", [filler[0], agreed]),
        StrategyRanking("openalex_search", [filler[1], agreed]),
    ])

    assert candidates[0].source_id == "src_agreed"


def test_the_same_paper_from_two_providers_collapses_to_one_candidate() -> None:
    """Without cross-provider dedupe, RRF double-counts and the duplicate outranks all."""
    from_s2 = make_record("src_s2_side", "The State of OA", doi="10.7717/peerj.4375")
    from_openalex = make_record("src_oa_side", "The state of OA", doi="10.7717/PEERJ.4375")

    candidates = fuse_candidates([
        StrategyRanking("s2_snippet", [from_s2]),
        StrategyRanking("openalex_search", [from_openalex]),
    ])

    assert len(candidates) == 1


def test_a_doi_less_paper_collapses_on_normalized_title_and_year() -> None:
    a = make_record("src_a", "Attention Is All You Need")
    b = make_record("src_b", "attention is all you need!")
    assert len(fuse_candidates([StrategyRanking("s2_snippet", [a, b])])) == 1


def test_everything_already_cited_is_subtracted() -> None:
    """The core of "missing work": a paper they already cite is not a finding."""
    cited = make_record("src_cited", "Already Cited", doi="10.1/cited")
    fresh = make_record("src_new", "Not Cited", doi="10.1/new")

    candidates = fuse_candidates(
        [StrategyRanking("s2_snippet", [cited, fresh])],
        already_cited=cited_keys_for([cited]),
    )

    assert [c.source_id for c in candidates] == ["src_new"]


def test_a_candidate_keeps_the_strategy_that_ranked_it_highest() -> None:
    """`Candidate.strategy` should answer "where did this come from?" informatively."""
    target = make_record("src_t", "Target", doi="10.1/t")
    filler = make_record("src_f", "Filler", doi="10.1/f")

    candidates = fuse_candidates([
        StrategyRanking("s2_snippet", [filler, target]),         # target at rank 1
        StrategyRanking("s2_recommendations", [target, filler]),  # target at rank 0
    ])

    by_id = {c.source_id: c for c in candidates}
    assert by_id["src_t"].strategy == "s2_recommendations"
    assert by_id["src_f"].strategy == "s2_snippet"


def test_fusion_ties_break_deterministically() -> None:
    """Two works each ranked 0 and 1 genuinely tie; the order must still be reproducible."""
    a = make_record("src_a", "A", doi="10.1/a")
    b = make_record("src_b", "B", doi="10.1/b")
    rankings = [
        StrategyRanking("s2_snippet", [a, b]),
        StrategyRanking("s2_recommendations", [b, a]),
    ]

    first = [c.source_id for c in fuse_candidates(rankings)]
    second = [c.source_id for c in fuse_candidates(list(reversed(rankings)))]
    assert first == second


# --------------------------------------------------------------------------- rerank


async def test_rerank_discards_a_source_id_it_was_not_given() -> None:
    """The reranker ranks what it was handed; it cannot introduce a source (HR-1)."""
    real = make_record("src_real", "Real", doi="10.1/real")
    candidates = fuse_candidates([StrategyRanking("s2_snippet", [real])])
    llm = FakeLLM([{"scores": [
        {"source_id": "src_real", "relevance": 0.9, "why": "directly tests the claim"},
        {"source_id": "src_hallucinated", "relevance": 1.0, "why": "invented"},
    ]}])
    reranker = Reranker(llm=llm, keep_top=5)

    result = await reranker.rerank(
        Claim(claim_id="clm_1", text="A claim.", span_id="spn_1", citability=0.9),
        candidates,
        {"src_real": real},
    )

    assert [c.source_id for c in result] == ["src_real"]
    assert reranker.discarded_unknown_source == 1


async def test_rerank_sorts_by_claim_relevance_not_by_fused_rank() -> None:
    """The whole point of ADR-005's rerank: topical rank is not claim relevance."""
    on_topic = make_record("src_topical", "Famous survey", doi="10.1/topical")
    on_claim = make_record("src_specific", "Tests exactly this", doi="10.1/specific")
    candidates = fuse_candidates([StrategyRanking("s2_snippet", [on_topic, on_claim])])
    assert candidates[0].source_id == "src_topical"  # fused rank favours the survey

    llm = FakeLLM([{"scores": [
        {"source_id": "src_topical", "relevance": 0.2, "why": "topic only"},
        {"source_id": "src_specific", "relevance": 0.95, "why": "tests the claim"},
    ]}])
    result = await Reranker(llm=llm).rerank(
        Claim(claim_id="clm_1", text="A claim.", span_id="spn_1", citability=0.9),
        candidates,
        {"src_topical": on_topic, "src_specific": on_claim},
    )

    assert [c.source_id for c in result] == ["src_specific", "src_topical"]


async def test_an_unscored_candidate_sorts_below_every_scored_one() -> None:
    """Unjudged is not the same as judged irrelevant."""
    a = make_record("src_a", "A", doi="10.1/a")
    b = make_record("src_b", "B", doi="10.1/b")
    candidates = fuse_candidates([StrategyRanking("s2_snippet", [a, b])])
    llm = FakeLLM([{"scores": [{"source_id": "src_b", "relevance": 0.1, "why": "weak"}]}])

    result = await Reranker(llm=llm).rerank(
        Claim(claim_id="clm_1", text="A claim.", span_id="spn_1", citability=0.9),
        candidates,
        {"src_a": a, "src_b": b},
    )

    assert [c.source_id for c in result] == ["src_b", "src_a"]


# --------------------------------------------------------------------------- HR-3 / model


def test_the_review_model_is_the_current_opus() -> None:
    assert DEFAULT_MODEL == "claude-opus-5"


def test_a_missing_anthropic_key_raises_at_the_point_of_use() -> None:
    """Not a startup key (HR-2 names two), but never an "if no key, skip" branch either."""
    with pytest.raises(MissingAPIKeyError) as exc:
        StructuredLLM(api_key=None)
    assert "no findings" in str(exc.value)


def test_no_review_module_writes_to_the_source_store() -> None:
    """HR-1, checked as a property of the package rather than trusted to review."""
    import pathlib

    review_dir = pathlib.Path(__file__).resolve().parents[3] / "app" / "review"
    offenders = [
        path.name
        for path in review_dir.glob("*.py")
        if ".put(" in path.read_text() or "store.put" in path.read_text()
    ]
    assert offenders == [], f"app/review must never call source_store.put(): {offenders}"


# --------------------------------------------------------------------------- streaming


class _FakeDocuments:
    def __init__(self, document: Document) -> None:
        self.document = document

    async def get(self, doc_id, version=None):
        return self.document if doc_id == self.document.doc_id else None


class _FakeSourceStore:
    def __init__(self, records: dict[str, SourceRecord]) -> None:
        self.records = records

    async def warm(self, ids):
        return None

    def get(self, source_id):
        return self.records.get(source_id)


class _FakeCandidates:
    def __init__(self, candidates) -> None:
        self.candidates = candidates
        self.snippets_by_source: dict[str, str] = {}

    async def prepare(self, context):
        return None

    async def generate(self, claim, context):
        return self.candidates

    def snippet_for(self, source_id):
        return None


class _FakeExtractor:
    def __init__(self, claims) -> None:
        self.claims = claims

    async def extract(self, document, target_ids):
        return self.claims


class _FakeReranker:
    def __init__(self, shortlist) -> None:
        self.shortlist = shortlist

    async def rerank(self, claim, candidates, records, snippets=None):
        return self.shortlist


class _FakeVerifier:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)

    async def verify_detailed(self, claim_text, record):
        return self.outcomes.pop(0)


def _runner(**kwargs) -> ReviewRunner:
    document = _doc_with(SPAN_TEXT)
    defaults = dict(
        document_store=_FakeDocuments(document),
        source_store=_FakeSourceStore({}),
        extractor=_FakeExtractor([]),
        candidates=_FakeCandidates([]),
        reranker=_FakeReranker([]),
        verifier=_FakeVerifier([]),
        check_existing_anchors=False,
    )
    defaults.update(kwargs)
    return ReviewRunner(**defaults)


async def test_zero_candidates_is_a_finding_not_silence() -> None:
    """"No missing work found for this claim" is a reported outcome (ADR-006)."""
    claim = Claim(claim_id="clm_1", text="A claim.", span_id="spn_1", citability=0.9)
    events = [e async for e in _runner(extractor=_FakeExtractor([claim])).stream("doc_1")]

    findings = [payload for name, payload in events if name == "finding"]
    assert len(findings) == 1
    assert findings[0]["kind"] == "no_candidates_found"
    assert findings[0]["severity"] == "info"


async def test_progress_reports_verified_over_total_throughout() -> None:
    claims = [
        Claim(claim_id=f"clm_{i}", text=f"Claim {i}.", span_id="spn_1", citability=0.9 - i / 10)
        for i in range(3)
    ]
    events = [e async for e in _runner(extractor=_FakeExtractor(claims)).stream("doc_1")]

    progress = [payload for name, payload in events if name == "progress"]
    assert progress[0]["claims_total"] == 3
    assert [p["claims_verified"] for p in progress] == [0, 1, 2, 3]
    assert events[-1][0] == "done"
    assert events[-1][1]["claims_verified"] == 3


async def test_a_quote_check_kill_is_counted_in_progress_not_hidden() -> None:
    """A short feed must be explicable: the counter is why it was short."""
    from app.review.verifier import VerificationOutcome

    claim = Claim(claim_id="clm_1", text="A claim.", span_id="spn_1", citability=0.9)
    record = make_record("src_x", "X", doi="10.1/x")
    candidates = fuse_candidates([StrategyRanking("s2_snippet", [record])])

    events = [
        e
        async for e in _runner(
            extractor=_FakeExtractor([claim]),
            source_store=_FakeSourceStore({"src_x": record}),
            candidates=_FakeCandidates(candidates),
            reranker=_FakeReranker(candidates),
            verifier=_FakeVerifier([VerificationOutcome(None, quote_check_failed=True)]),
        ).stream("doc_1")
    ]

    assert [payload for name, payload in events if name == "finding"] == []
    assert events[-1][1]["quote_check_failures"] == 1


async def test_an_unverifiable_candidate_is_still_shown() -> None:
    """ADR-006: `unverifiable_no_abstract` is a displayed outcome, not a silent skip."""
    from app.core.contracts import Verification
    from app.review.verifier import VerificationOutcome

    claim = Claim(claim_id="clm_1", text="A claim.", span_id="spn_1", citability=0.9)
    record = make_record("src_x", "X", doi="10.1/x")
    candidates = fuse_candidates([StrategyRanking("s2_snippet", [record])])
    outcome = VerificationOutcome(
        Verification(
            label=VerificationLabel.UNVERIFIABLE_NO_ABSTRACT,
            quote=None,
            abstract_source=AbstractSource.UNAVAILABLE,
            confidence=1.0,
        )
    )

    events = [
        e
        async for e in _runner(
            extractor=_FakeExtractor([claim]),
            source_store=_FakeSourceStore({"src_x": record}),
            candidates=_FakeCandidates(candidates),
            reranker=_FakeReranker(candidates),
            verifier=_FakeVerifier([outcome]),
        ).stream("doc_1")
    ]

    findings = [payload for name, payload in events if name == "finding"]
    assert len(findings) == 1
    assert findings[0]["verification"]["label"] == "unverifiable_no_abstract"
    assert events[-1][1]["unverifiable_no_abstract"] == 1


async def test_a_candidate_that_does_not_address_the_claim_is_counted_not_emitted() -> None:
    from app.core.contracts import Verification
    from app.review.verifier import VerificationOutcome

    claim = Claim(claim_id="clm_1", text="A claim.", span_id="spn_1", citability=0.9)
    record = make_record("src_x", "X", doi="10.1/x")
    candidates = fuse_candidates([StrategyRanking("s2_snippet", [record])])
    outcome = VerificationOutcome(
        Verification(
            label=VerificationLabel.DOES_NOT_ADDRESS,
            quote="a real quote from the abstract goes here",
            abstract_source=AbstractSource.S2,
            confidence=0.8,
        )
    )

    events = [
        e
        async for e in _runner(
            extractor=_FakeExtractor([claim]),
            source_store=_FakeSourceStore({"src_x": record}),
            candidates=_FakeCandidates(candidates),
            reranker=_FakeReranker(candidates),
            verifier=_FakeVerifier([outcome]),
        ).stream("doc_1")
    ]

    assert [payload for name, payload in events if name == "finding"] == []
    assert events[-1][1]["candidates_rejected"] == 1


async def test_findings_arrive_in_descending_citability_order() -> None:
    """ADR-014's ordering guarantee, end to end."""
    from app.core.contracts import Verification
    from app.review.verifier import VerificationOutcome

    claims = [
        Claim(claim_id="clm_high", text="High.", span_id="spn_1", citability=0.95),
        Claim(claim_id="clm_low", text="Low.", span_id="spn_1", citability=0.30),
    ]
    record = make_record("src_x", "X", doi="10.1/x")
    candidates = fuse_candidates([StrategyRanking("s2_snippet", [record])])
    supports = VerificationOutcome(
        Verification(
            label=VerificationLabel.SUPPORTS,
            quote="a real quote from the abstract goes here",
            abstract_source=AbstractSource.S2,
            confidence=0.9,
        )
    )

    events = [
        e
        async for e in _runner(
            extractor=_FakeExtractor(claims),
            source_store=_FakeSourceStore({"src_x": record}),
            candidates=_FakeCandidates(candidates),
            reranker=_FakeReranker(candidates),
            verifier=_FakeVerifier([supports, supports]),
        ).stream("doc_1")
    ]

    findings = [payload for name, payload in events if name == "finding"]
    assert [f["claim"]["citability"] for f in findings] == [0.95, 0.30]
    assert findings[0]["severity"] == "high"
    assert findings[0]["verification"]["quote"]


async def test_an_existing_anchor_that_does_not_support_its_claim_is_a_finding() -> None:
    """The verifier's second caller (ADR-006): checking the citations already there."""
    from app.core.contracts import Verification
    from app.review.verifier import VerificationOutcome

    anchors = [CitationAnchor(anchor_id="anc_1", source_ids=["src_cited"], offset_in_span=20)]
    document = _doc_with(SPAN_TEXT, anchors)
    claim = Claim(
        claim_id="clm_1", text="A claim.", span_id="spn_1",
        anchor_ids=["anc_1"], citability=0.9,
    )
    record = make_record("src_cited", "Cited", doi="10.1/cited")
    mismatch = VerificationOutcome(
        Verification(
            label=VerificationLabel.DOES_NOT_ADDRESS,
            quote="a real quote from the abstract goes here",
            abstract_source=AbstractSource.S2,
            confidence=0.9,
        )
    )

    runner = ReviewRunner(
        document_store=_FakeDocuments(document),
        source_store=_FakeSourceStore({"src_cited": record}),
        extractor=_FakeExtractor([claim]),
        candidates=_FakeCandidates([]),
        reranker=_FakeReranker([]),
        verifier=_FakeVerifier([mismatch]),
        check_existing_anchors=True,
    )
    events = [e async for e in runner.stream("doc_1")]

    findings = [payload for name, payload in events if name == "finding"]
    mismatches = [f for f in findings if f["kind"] == "claim_citation_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["source_id"] == "src_cited"
    assert mismatches[0]["severity"] == "high"


async def test_a_supported_existing_anchor_produces_no_finding() -> None:
    from app.core.contracts import Verification
    from app.review.verifier import VerificationOutcome

    anchors = [CitationAnchor(anchor_id="anc_1", source_ids=["src_cited"], offset_in_span=20)]
    claim = Claim(
        claim_id="clm_1", text="A claim.", span_id="spn_1",
        anchor_ids=["anc_1"], citability=0.9,
    )
    record = make_record("src_cited", "Cited", doi="10.1/cited")
    supports = VerificationOutcome(
        Verification(
            label=VerificationLabel.SUPPORTS,
            quote="a real quote from the abstract goes here",
            abstract_source=AbstractSource.S2,
            confidence=0.95,
        )
    )

    runner = ReviewRunner(
        document_store=_FakeDocuments(_doc_with(SPAN_TEXT, anchors)),
        source_store=_FakeSourceStore({"src_cited": record}),
        extractor=_FakeExtractor([claim]),
        candidates=_FakeCandidates([]),
        reranker=_FakeReranker([]),
        verifier=_FakeVerifier([supports]),
        check_existing_anchors=True,
    )
    events = [e async for e in runner.stream("doc_1")]

    kinds = {p["kind"] for name, p in events if name == "finding"}
    assert "claim_citation_mismatch" not in kinds


async def test_no_citable_claims_ends_with_an_explicit_reason() -> None:
    events = [e async for e in _runner().stream("doc_1")]
    assert events[-1] == ("done", {**events[-1][1]})
    assert events[-1][1]["reason"] == "no_citable_claims"
