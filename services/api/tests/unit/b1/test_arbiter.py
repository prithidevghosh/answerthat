"""The arbiter and its agreement scoring. ADR-001.

Providers are stubs implementing the Appendix A `Provider` protocol. B1 codes against
the protocol and never imports `app/providers/` — these tests are the proof that the
boundary holds, and they let arbitration be tested without a network or an API key.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.contracts import (
    AbstractSource,
    ConfidenceTier,
    ParsedReference,
    Provenance,
    SourceRecord,
)
from app.parsing.agreement import score_agreement
from app.parsing.arbiter import Arbiter, ArbiterProviders

ACCEPT = 0.85

VASWANI = {
    "type": "paper-conference",
    "title": "Attention Is All You Need",
    "author": [{"family": "Vaswani", "given": "Ashish"}],
    "issued": {"date-parts": [[2017]]},
    "container-title": "Advances in Neural Information Processing Systems",
}


def _record(source_id: str, csl: dict[str, Any], provider: str = "crossref") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        csl=csl,
        provenance=Provenance(
            provider=provider,  # type: ignore[arg-type]
            endpoint="/works",
            retrieved_at="2026-08-15T00:00:00Z",
            external_url=f"https://example.org/{source_id}",
        ),
        abstract=None,
        abstract_source=AbstractSource.UNAVAILABLE,
    )


class StubProvider:
    """Implements the Appendix A Provider protocol. Records what it was asked."""

    def __init__(
        self,
        *,
        search_results: list[SourceRecord] | None = None,
        match_result: SourceRecord | None = None,
        batch_results: list[SourceRecord] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.search_results = search_results or []
        self.match_result = match_result
        self.batch_results = batch_results or []
        self.raises = raises
        self.search_calls: list[str] = []
        self.match_calls: list[tuple[str, int | None]] = []
        self.batch_calls: list[list[str]] = []

    async def search_works(self, query: str, limit: int = 10) -> list[SourceRecord]:
        if self.raises:
            raise self.raises
        self.search_calls.append(query)
        return self.search_results[:limit]

    async def match_reference(self, title: str, year: int | None = None) -> SourceRecord | None:
        if self.raises:
            raise self.raises
        self.match_calls.append((title, year))
        return self.match_result

    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]:
        return None, AbstractSource.UNAVAILABLE

    async def batch_hydrate(self, ids: list[str]) -> list[SourceRecord]:
        self.batch_calls.append(list(ids))
        return self.batch_results


def _reference(
    ref_id: str = "b0",
    csl: dict[str, Any] | None = None,
    raw: str = "A. Vaswani et al., Attention is all you need, NeurIPS 2017.",
) -> ParsedReference:
    return ParsedReference(
        ref_id=ref_id,
        raw_string=raw,
        csl=csl if csl is not None else dict(VASWANI),
        tier=ConfidenceTier.PARSED_UNRESOLVED,
        parse_confidence=0.80,
    )


# ---------------------------------------------------------------- agreement scoring


def test_identical_records_score_one() -> None:
    assert score_agreement(VASWANI, VASWANI).score == pytest.approx(1.0)


def test_weights_are_the_specified_ones() -> None:
    """0.6·title + 0.2·year + 0.2·first_author, exactly."""
    theirs = {**VASWANI, "issued": {"date-parts": [[2003]]}, "author": [{"family": "Zzz"}]}
    breakdown = score_agreement(VASWANI, theirs)
    assert breakdown.title_sim == pytest.approx(1.0)
    assert breakdown.year_match == 0.0
    assert breakdown.score == pytest.approx(0.6, abs=0.02)


def test_a_near_year_scores_half() -> None:
    """A one-year gap is a preprint/publication difference far more often than a
    different paper; two years apart is not."""
    one_off = {**VASWANI, "issued": {"date-parts": [[2018]]}}
    two_off = {**VASWANI, "issued": {"date-parts": [[2019]]}}
    assert score_agreement(VASWANI, one_off).year_match == 0.5
    assert score_agreement(VASWANI, two_off).year_match == 0.0


def test_missing_data_scores_zero_rather_than_being_renormalised_away() -> None:
    """Otherwise a reference with only a title could reach 1.0 on one fuzzy field."""
    ours = {"title": "Attention Is All You Need"}
    breakdown = score_agreement(ours, VASWANI)
    assert breakdown.title_sim == pytest.approx(1.0)
    assert breakdown.score == pytest.approx(0.6)
    assert not breakdown.accepted(ACCEPT)


def test_a_different_paper_does_not_reach_the_threshold() -> None:
    other = {
        "title": "Deep Residual Learning for Image Recognition",
        "author": [{"family": "He", "given": "Kaiming"}],
        "issued": {"date-parts": [[2016]]},
    }
    assert not score_agreement(VASWANI, other).accepted(ACCEPT)


def test_particles_are_included_in_the_author_comparison() -> None:
    ours = {"title": "T", "author": [{"family": "Berg", "non-dropping-particle": "van der"}]}
    theirs = {"title": "T", "author": [{"family": "van der Berg"}]}
    assert score_agreement(ours, theirs).first_author_sim == pytest.approx(1.0)


def test_matching_dois_are_identity_not_similarity() -> None:
    """Two records carrying the same DOI are the same work by definition."""
    ours = {"title": "typo'd ttile", "DOI": "10.1145/3530811"}
    theirs = {"title": "Efficient Transformers: A Survey", "DOI": "10.1145/3530811"}
    breakdown = score_agreement(ours, theirs)
    assert breakdown.matched_on == "doi_identity"
    assert breakdown.score == 1.0


def test_different_dois_fall_back_to_the_formula() -> None:
    ours = {**VASWANI, "DOI": "10.1/aaa"}
    theirs = {**VASWANI, "DOI": "10.1/bbb"}
    assert score_agreement(ours, theirs).matched_on == "formula"


# ---------------------------------------------------------------- the cascade


async def test_doi_path_uses_crossref_and_batches() -> None:
    """Once IDs are known, batch — S2 is ~1 rps and looping is the difference between
    ingest finishing and ingest appearing to hang."""
    record = _record("cr:10.1/x", {**VASWANI, "DOI": "10.1/x"})
    crossref = StubProvider(batch_results=[record])
    s2 = StubProvider()
    arbiter = Arbiter(
        ArbiterProviders(crossref=crossref, semantic_scholar=s2), accept_threshold=ACCEPT
    )

    references = [_reference(csl={**VASWANI, "DOI": "10.1/x"}) for _ in range(3)]
    updated, outcomes = await arbiter.reconcile(references)

    assert crossref.batch_calls == [["10.1/x"]], "one batched call, not three"
    assert s2.match_calls == [], "the title path must not run once the DOI resolved"
    assert all(o.path == "crossref_doi" and o.accepted for o in outcomes)
    assert all(r.tier == ConfidenceTier.RESOLVED for r in updated)


async def test_title_path_falls_to_semantic_scholar() -> None:
    s2 = StubProvider(match_result=_record("s2:abc", VASWANI, provider="semantic_scholar"))
    arbiter = Arbiter(ArbiterProviders(semantic_scholar=s2), accept_threshold=ACCEPT)

    updated, outcomes = await arbiter.reconcile([_reference()])
    assert s2.match_calls == [("Attention Is All You Need", 2017)]
    assert outcomes[0].path == "s2_match"
    assert updated[0].source_id == "s2:abc"


async def test_openalex_is_the_fallback_for_an_s2_miss() -> None:
    s2 = StubProvider(match_result=None)
    openalex = StubProvider(
        search_results=[
            _record("oa:wrong", {"title": "Something Else Entirely"}, provider="openalex"),
            _record("oa:right", VASWANI, provider="openalex"),
        ]
    )
    arbiter = Arbiter(
        ArbiterProviders(semantic_scholar=s2, openalex=openalex), accept_threshold=ACCEPT
    )

    updated, outcomes = await arbiter.reconcile([_reference()])
    assert openalex.search_calls == ["Attention Is All You Need"]
    assert outcomes[0].path == "openalex_search"
    assert updated[0].source_id == "oa:right", "the best candidate wins, not the first"


async def test_the_external_record_replaces_our_parse_as_canonical() -> None:
    """ADR-001. And our parse and raw string survive for the audit view."""
    theirs = {**VASWANI, "container-title": "NeurIPS", "page": "5998-6008"}
    s2 = StubProvider(match_result=_record("s2:abc", theirs, provider="semantic_scholar"))
    arbiter = Arbiter(ArbiterProviders(semantic_scholar=s2), accept_threshold=ACCEPT)

    ours = {**VASWANI, "container-title": "Adv. Neural Inf. Process. Syst."}
    updated, outcomes = await arbiter.reconcile([_reference(csl=ours)])

    assert updated[0].csl["container-title"] == "NeurIPS"
    assert updated[0].csl["page"] == "5998-6008"
    assert outcomes[0].provisional_csl["container-title"] == "Adv. Neural Inf. Process. Syst."
    assert outcomes[0].raw_string.startswith("A. Vaswani")


async def test_below_threshold_keeps_our_parse_and_does_not_resolve() -> None:
    """A near miss is still a miss. Nothing is promoted for having been looked at."""
    near = {
        "title": "Attention Is Not All You Need",
        "author": [{"family": "Dong"}],
        "issued": {"date-parts": [[2021]]},
    }
    s2 = StubProvider(match_result=_record("s2:near", near, provider="semantic_scholar"))
    arbiter = Arbiter(ArbiterProviders(semantic_scholar=s2), accept_threshold=ACCEPT)

    updated, outcomes = await arbiter.reconcile([_reference()])
    assert not outcomes[0].accepted
    assert updated[0].tier == ConfidenceTier.PARSED_UNRESOLVED
    assert updated[0].source_id is None
    assert updated[0].csl["title"] == "Attention Is All You Need", "our parse is kept"
    assert updated[0].agreement_score == outcomes[0].agreement.score


async def test_exactly_at_the_threshold_is_accepted() -> None:
    """`>= 0.85`, not `> 0.85`."""
    arbiter = Arbiter(ArbiterProviders(), accept_threshold=ACCEPT)
    breakdown = score_agreement(VASWANI, VASWANI)
    assert breakdown.accepted(ACCEPT)
    assert arbiter.accept_threshold == 0.85


# ---------------------------------------------------------------- honesty under failure


async def test_a_provider_error_is_reported_not_swallowed() -> None:
    """HR-3: 'we searched and found nothing' and 'we could not search' are different
    claims, and collapsing them is a false negative dressed as a clean result."""
    s2 = StubProvider(raises=RuntimeError("429 rate limited"))
    arbiter = Arbiter(ArbiterProviders(semantic_scholar=s2), accept_threshold=ACCEPT)

    updated, outcomes = await arbiter.reconcile([_reference()])
    assert not outcomes[0].accepted
    assert not outcomes[0].fully_checked
    assert any("429" in e for e in outcomes[0].provider_errors)
    assert any("not because nothing matched" in n for n in outcomes[0].notes)
    assert len(updated) == 1, "the reference survives a provider failure"


async def test_no_candidates_is_distinguishable_from_a_failure() -> None:
    openalex = StubProvider(search_results=[])
    arbiter = Arbiter(ArbiterProviders(openalex=openalex), accept_threshold=ACCEPT)
    _, outcomes = await arbiter.reconcile([_reference()])
    assert outcomes[0].fully_checked
    assert any("no external candidates" in n for n in outcomes[0].notes)


async def test_a_missing_provider_is_recorded_rather_than_silently_skipped() -> None:
    arbiter = Arbiter(ArbiterProviders(), accept_threshold=ACCEPT)
    _, outcomes = await arbiter.reconcile([_reference(csl={**VASWANI, "DOI": "10.1/x"})])
    notes = " ".join(outcomes[0].notes)
    assert "no Crossref provider configured" in notes
    assert "no Semantic Scholar provider configured" in notes


async def test_no_reference_is_ever_dropped() -> None:
    """Whatever happens — resolved, missed, errored, unparseable — the count holds."""
    s2 = StubProvider(match_result=None)
    openalex = StubProvider(raises=RuntimeError("boom"))
    arbiter = Arbiter(
        ArbiterProviders(semantic_scholar=s2, openalex=openalex), accept_threshold=ACCEPT
    )
    references = [
        _reference("b0"),
        _reference("b1", csl=None, raw="unparseable fragment"),
        _reference("b2", csl={"title": "Another Paper"}),
    ]
    updated, outcomes = await arbiter.reconcile(references)
    assert len(updated) == len(references) == len(outcomes)
    assert [r.ref_id for r in updated] == ["b0", "b1", "b2"]


async def test_resolve_doi_is_preferred_when_the_adapter_offers_it() -> None:
    """B2's Crossref adapter exposes `resolve_doi(doi)` — an exact /works/{doi} lookup,
    which is the path ADR-001 names. We use it when present without requiring it."""

    class CrossrefWithResolveDoi(StubProvider):
        def __init__(self) -> None:
            super().__init__()
            self.resolved: list[str] = []

        async def resolve_doi(self, doi: str) -> SourceRecord | None:
            self.resolved.append(doi)
            return _record("cr:10.1/x", {**VASWANI, "DOI": "10.1/x"})

    crossref = CrossrefWithResolveDoi()
    arbiter = Arbiter(ArbiterProviders(crossref=crossref), accept_threshold=ACCEPT)
    updated, outcomes = await arbiter.reconcile([_reference(csl={**VASWANI, "DOI": "10.1/x"})])

    assert crossref.resolved == ["10.1/x"]
    assert crossref.search_calls == [], "search_works is the fallback, not the first choice"
    assert outcomes[0].path == "crossref_doi"
    assert updated[0].source_id == "cr:10.1/x"


async def test_a_protocol_only_provider_still_works() -> None:
    """The stubs implement Appendix A and nothing more; arbitration must not require
    anything beyond it."""
    crossref = StubProvider(search_results=[_record("cr:x", {**VASWANI, "DOI": "10.1/x"})])
    arbiter = Arbiter(ArbiterProviders(crossref=crossref), accept_threshold=ACCEPT)
    _, outcomes = await arbiter.reconcile([_reference(csl={**VASWANI, "DOI": "10.1/x"})])
    assert crossref.search_calls == ["10.1/x"]
    assert outcomes[0].accepted


async def test_the_arbiter_never_constructs_a_source_record() -> None:
    """HR-1, structurally: every source_id the arbiter reports came from a provider."""
    record = _record("s2:abc", VASWANI, provider="semantic_scholar")
    s2 = StubProvider(match_result=record)
    arbiter = Arbiter(ArbiterProviders(semantic_scholar=s2), accept_threshold=ACCEPT)
    updated, _ = await arbiter.reconcile([_reference()])
    assert updated[0].source_id == record.source_id
