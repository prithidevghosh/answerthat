"""Appendix A's `Provider` protocol, and the candidate generator that calls through it.

`Provider` is a plain `Protocol` (not `@runtime_checkable`), so nothing checks
conformance at runtime and a renamed method would only surface when B1's arbiter calls
it. These tests are that check.
"""

from __future__ import annotations

import inspect

import pytest

from app.core.contracts import Claim
from app.providers.crossref import CrossrefProvider
from app.providers.openalex import OpenAlexProvider
from app.providers.semantic_scholar import SemanticScholarProvider
from app.review.candidates import CandidateGenerator, DocumentContext

PROVIDER_METHODS = ("search_works", "match_reference", "get_abstract", "batch_hydrate")
ADAPTERS = [SemanticScholarProvider, OpenAlexProvider, CrossrefProvider]


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.name)
@pytest.mark.parametrize("method", PROVIDER_METHODS)
def test_every_adapter_implements_the_provider_protocol(adapter, method) -> None:
    fn = getattr(adapter, method, None)
    assert fn is not None, f"{adapter.__name__} is missing {method}"
    assert inspect.iscoroutinefunction(fn), f"{adapter.__name__}.{method} must be async"


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.name)
def test_provider_signatures_match_appendix_a(adapter) -> None:
    """B1's arbiter calls these positionally; a renamed parameter breaks it silently."""
    assert list(inspect.signature(adapter.search_works).parameters)[1:] == ["query", "limit"]
    assert list(inspect.signature(adapter.match_reference).parameters)[1:] == ["title", "year"]
    assert list(inspect.signature(adapter.get_abstract).parameters)[1:] == ["source_id"]
    assert list(inspect.signature(adapter.batch_hydrate).parameters)[1:] == ["ids"]


# --------------------------------------------------------------------------- generation


class _FakeS2:
    """Records which strategies were invoked, and with what."""

    def __init__(self, snippets=(), recommendations=()) -> None:
        self._snippets = list(snippets)
        self._recommendations = list(recommendations)
        self.snippet_queries: list[str] = []
        self.recommendation_seeds: list[list[str]] = []

    async def snippet_search(self, query, limit=10):
        self.snippet_queries.append(query)
        return self._snippets

    async def recommendations_from(self, seeds, limit=20, negative_paper_ids=None):
        self.recommendation_seeds.append(list(seeds))
        return self._recommendations


class _FakeOpenAlex:
    def __init__(self, search=(), graph=()) -> None:
        self._search = list(search)
        self._graph = list(graph)
        self.search_queries: list[str] = []
        self.expansion_seeds: list[list[str]] = []

    async def search_works(self, query, limit=10):
        self.search_queries.append(query)
        return self._search

    async def one_hop_expansion(self, ids, limit=50):
        self.expansion_seeds.append(list(ids))
        return self._graph


class _Snippet:
    def __init__(self, source_id, text, record) -> None:
        self.source_id = source_id
        self.text = text
        self.record = record


@pytest.fixture
def claim() -> Claim:
    return Claim(
        claim_id="clm_1",
        text="Transformer models dominate sequence modelling.",
        span_id="spn_1",
        citability=0.9,
    )


async def test_all_three_strategies_run_for_a_claim(claim, source_record) -> None:
    """ADR-005 names three strategies; a silently-missing one narrows the search."""
    cited = source_record("src_cited", "Cited work", "10.1/cited")
    snippet_hit = source_record("src_snip", "Snippet hit", "10.1/snip")
    rec_hit = source_record("src_rec", "Recommendation hit", "10.1/rec")
    search_hit = source_record("src_oa", "OpenAlex hit", "10.1/oa")
    graph_hit = source_record("src_graph", "Graph hit", "10.1/graph")

    s2 = _FakeS2(
        snippets=[_Snippet("src_snip", "a matched passage", snippet_hit)],
        recommendations=[rec_hit],
    )
    openalex = _FakeOpenAlex(search=[search_hit], graph=[graph_hit])
    generator = CandidateGenerator(semantic_scholar=s2, openalex=openalex)

    context = DocumentContext([cited])
    context.s2_seed_ids = ["s2_seed"]
    context.openalex_seed_ids = ["W1"]

    await generator.prepare(context)
    candidates = await generator.generate(claim, context)

    # Every strategy was actually invoked, with the claim (not the section) as the query.
    assert s2.snippet_queries == [claim.text]
    assert openalex.search_queries == [claim.text]
    assert s2.recommendation_seeds == [["s2_seed"]]
    assert openalex.expansion_seeds == [["W1"]]

    found = {c.source_id for c in candidates}
    assert found == {"src_snip", "src_rec", "src_oa", "src_graph"}


async def test_recommendations_are_seeded_once_per_document_not_per_claim(
    claim, source_record
) -> None:
    """The SPECTER2 neighbourhood is claim-independent; re-asking spends the rate limit."""
    s2 = _FakeS2(recommendations=[source_record("src_rec", "Rec", "10.1/rec")])
    openalex = _FakeOpenAlex()
    generator = CandidateGenerator(semantic_scholar=s2, openalex=openalex)

    context = DocumentContext([])
    context.s2_seed_ids = ["s2_seed"]

    await generator.prepare(context)
    await generator.generate(claim, context)
    await generator.generate(claim, context)

    assert len(s2.recommendation_seeds) == 1


async def test_a_paper_the_authors_already_cite_is_not_a_candidate(claim, source_record) -> None:
    cited = source_record("src_cited", "Already cited", "10.1/cited")
    s2 = _FakeS2(snippets=[_Snippet("src_cited", "passage", cited)])
    generator = CandidateGenerator(semantic_scholar=s2, openalex=_FakeOpenAlex())

    candidates = await generator.generate(claim, DocumentContext([cited]))

    assert candidates == []


async def test_the_matched_passage_is_kept_for_the_finding(claim, source_record) -> None:
    """A finding should show *why* a paper is relevant, in real retrieved text."""
    hit = source_record("src_snip", "Snippet hit", "10.1/snip")
    s2 = _FakeS2(snippets=[_Snippet("src_snip", "self-attention scales quadratically", hit)])
    generator = CandidateGenerator(semantic_scholar=s2, openalex=_FakeOpenAlex())

    await generator.generate(claim, DocumentContext([]))

    assert generator.snippet_for("src_snip") == "self-attention scales quadratically"
    assert generator.snippet_for("src_absent") is None


async def test_a_throttled_strategy_raises_rather_than_narrowing_the_search(
    claim, source_record
) -> None:
    """Two working strategies plus one throttled one must not look like a thin literature."""
    from app.core.contracts import ProviderRateLimited

    class _Throttled(_FakeOpenAlex):
        async def search_works(self, query, limit=10):
            raise ProviderRateLimited("openalex throttled")

    generator = CandidateGenerator(semantic_scholar=_FakeS2(), openalex=_Throttled())

    with pytest.raises(ProviderRateLimited):
        await generator.generate(claim, DocumentContext([]))


async def test_a_document_with_no_resolved_bibliography_skips_the_seeded_strategies(
    claim,
) -> None:
    """No seeds is a real property of the paper, not an error — and not a fake result."""
    s2 = _FakeS2()
    openalex = _FakeOpenAlex()
    generator = CandidateGenerator(semantic_scholar=s2, openalex=openalex)

    context = DocumentContext([])
    assert context.has_bibliography is False

    await generator.prepare(context)
    await generator.generate(claim, context)

    assert s2.recommendation_seeds == []
    assert openalex.expansion_seeds == []
