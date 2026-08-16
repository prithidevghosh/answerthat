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
    """Records which strategies were invoked, and with what.

    `search_pool_available` mirrors the real adapter's property: keyed deployments have
    `/snippet/search`, unauthenticated ones do not. It defaults to True so that existing
    tests keep exercising the four-strategy path.
    """

    def __init__(self, snippets=(), recommendations=(), search_pool_available=True) -> None:
        self._snippets = list(snippets)
        self._recommendations = list(recommendations)
        self.snippet_queries: list[str] = []
        self.recommendation_seeds: list[list[str]] = []
        self.search_pool_available = search_pool_available

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


# --------------------------------------------- an unauthenticated deployment (ADR-010a, amended)


async def test_without_a_key_the_snippet_strategy_is_dropped_not_attempted(
    claim, source_record
) -> None:
    """The bug this replaces: one closed endpoint took every claim down with it.

    `/snippet/search` needs a key, and `asyncio.gather` here has no `return_exceptions`,
    so calling it unauthenticated aborted the review at its first claim. It is now left
    out of the run, and the other strategies still produce candidates.
    """
    s2 = _FakeS2(snippets=[source_record("src_snip", "A snippet paper", "10.1/snip")],
                 search_pool_available=False)
    openalex = _FakeOpenAlex(search=[source_record("src_oa", "An OpenAlex paper", "10.1/oa")])
    generator = CandidateGenerator(semantic_scholar=s2, openalex=openalex)

    candidates = await generator.generate(claim, DocumentContext([]))

    assert s2.snippet_queries == [], "a closed endpoint must not be called"
    assert [c.source_id for c in candidates] == ["src_oa"]
    assert {c.strategy for c in candidates} == {"openalex_search"}


async def test_the_dropped_strategy_is_reported_not_silently_missing(claim) -> None:
    """A three-strategy review and a four-strategy one must not look identical.

    This is the same honesty the module already applies to a missing bibliography, for a
    second reason the caller cannot otherwise see.
    """
    generator = CandidateGenerator(
        semantic_scholar=_FakeS2(search_pool_available=False), openalex=_FakeOpenAlex()
    )

    assert generator.snippet_search_available is False
    assert generator.strategies_for(DocumentContext([])) == ("openalex_search",)


async def test_an_unauthenticated_run_still_uses_recommendations_and_graph(
    claim, source_record
) -> None:
    """What survives without a key, asserted rather than assumed.

    Recommendations is ADR-005's "only genuinely semantic signal", and it is on the pool
    that answers anonymously — so the strategy the design leans on hardest is the one the
    missing key does not touch.
    """
    cited = source_record("src_cited", "A cited paper", "10.1/cited")
    cited.csl["custom"] = {"s2_paper_id": "s2id_a", "openalex_id": "W1"}
    recommended = source_record("src_rec", "A recommended paper", "10.1/rec")

    s2 = _FakeS2(recommendations=[recommended], search_pool_available=False)
    openalex = _FakeOpenAlex(graph=[source_record("src_hop", "A one-hop paper", "10.1/hop")])
    generator = CandidateGenerator(semantic_scholar=s2, openalex=openalex)

    context = DocumentContext([cited])
    await generator.prepare(context)
    candidates = await generator.generate(claim, context)

    assert s2.recommendation_seeds == [["s2id_a"]]
    assert generator.strategies_for(context) == (
        "s2_recommendations", "openalex_search", "openalex_graph",
    )
    assert {c.strategy for c in candidates} == {"s2_recommendations", "openalex_graph"}


async def test_dropping_a_strategy_does_not_soften_a_throttled_one(claim) -> None:
    """The gate narrows the strategy set; it does not turn failures into empty results.

    An available strategy that raises must still take the review with it, or ADR-010's
    invariant is gone — a thin result would again be indistinguishable from a thin
    literature. Unavailable and failed are handled differently on purpose.
    """
    from app.core.contracts import ProviderRateLimited

    class _Throttled(_FakeOpenAlex):
        async def search_works(self, query, limit=10):
            raise ProviderRateLimited("openalex throttled")

    generator = CandidateGenerator(
        semantic_scholar=_FakeS2(search_pool_available=False), openalex=_Throttled()
    )

    with pytest.raises(ProviderRateLimited):
        await generator.generate(claim, DocumentContext([]))
