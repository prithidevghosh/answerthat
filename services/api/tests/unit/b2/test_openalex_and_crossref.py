"""OpenAlex and Crossref adapters, plus the abstract fallback chain.

The OpenAlex tests concentrate on the three things that are easy to get wrong: the
inverted index, credit metering, and the direction of the two citation filters.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.contracts import AbstractSource, MissingAPIKeyError
from app.core.errors import ConfigurationError
from app.providers.abstracts import AbstractResolver
from app.providers.crossref import CrossrefProvider
from app.providers.openalex import MAX_OR_VALUES, OpenAlexProvider, invert_abstract
from app.providers.ratelimit import CreditBudget, OpenAlexCost

FAKE_KEY = "test-openalex-key"
FAKE_MAILTO = "tests@answerthat.local"


@pytest.fixture
def oa(cache, store, fast_limiter, transport_for):
    def build(routes=None, *, handler=None, **kwargs):
        transport = transport_for(routes, handler=handler)
        provider = OpenAlexProvider(
            api_key=FAKE_KEY,
            mailto=FAKE_MAILTO,
            cache=cache,
            store=store,
            limiter=fast_limiter,
            client=httpx.AsyncClient(transport=transport),
            **kwargs,
        )
        return provider, transport

    return build


@pytest.fixture
def crossref(cache, store, fast_limiter, transport_for):
    def build(routes=None, *, handler=None):
        transport = transport_for(routes, handler=handler)
        provider = CrossrefProvider(
            mailto=FAKE_MAILTO,
            cache=cache,
            store=store,
            limiter=fast_limiter,
            client=httpx.AsyncClient(transport=transport),
        )
        return provider, transport

    return build


# --------------------------------------------------------------------------- HR-2


@pytest.mark.parametrize("absent", [None, "", "   "])
def test_openalex_requires_a_key(cache, store, absent) -> None:
    with pytest.raises(MissingAPIKeyError):
        OpenAlexProvider(api_key=absent, mailto=FAKE_MAILTO, cache=cache, store=store)


def test_openalex_requires_a_polite_pool_address(cache, store) -> None:
    """Outside the polite pool, throttling presents as sparse results — same failure."""
    with pytest.raises(MissingAPIKeyError) as exc:
        OpenAlexProvider(api_key=FAKE_KEY, mailto=None, cache=cache, store=store)
    assert "polite pool" in str(exc.value)


def test_crossref_requires_a_polite_pool_address(cache, store) -> None:
    with pytest.raises(MissingAPIKeyError):
        CrossrefProvider(mailto=None, cache=cache, store=store)


async def test_every_openalex_call_carries_key_and_mailto(oa, openalex_work) -> None:
    provider, transport = oa({"/works": {"results": [openalex_work]}})
    await provider.search_works("open access")
    params = transport.requests[0].url.params
    assert params["api_key"] == FAKE_KEY
    assert params["mailto"] == FAKE_MAILTO
    assert f"mailto:{FAKE_MAILTO}" in transport.requests[0].headers["User-Agent"]


async def test_crossref_calls_carry_mailto(crossref) -> None:
    provider, transport = crossref({"/works": {"message": {"items": []}}})
    await provider.search_works("x")
    assert transport.requests[0].url.params["mailto"] == FAKE_MAILTO


# --------------------------------------------------------------------------- inverted index


def test_invert_abstract_reconstructs_the_text() -> None:
    index = {"the": [0, 3], "quick": [1], "brown": [2], "fox": [4]}
    assert invert_abstract(index) == "the quick brown the fox"


def test_invert_abstract_tolerates_sparse_positions() -> None:
    """OpenAlex drops some tokens, leaving gaps. A preallocated list would hole or throw."""
    assert invert_abstract({"alpha": [0], "omega": [7]}) == "alpha omega"


@pytest.mark.parametrize("empty", [None, {}, {"token": []}])
def test_invert_abstract_reports_nothing_rather_than_empty_string(empty) -> None:
    assert invert_abstract(empty) is None


async def test_openalex_records_carry_the_inverted_abstract(oa, openalex_work) -> None:
    provider, _ = oa({"/works": {"results": [openalex_work]}})
    (record,) = await provider.search_works("open access")
    assert record.abstract_source is AbstractSource.OPENALEX_INVERTED
    assert record.abstract.startswith("Despite growing interest in open access,")
    assert record.provenance.external_url == "https://doi.org/10.7717/peerj.4375"


# --------------------------------------------------------------------------- credits


async def test_a_list_query_costs_ten_credits_and_a_singleton_one(
    cache, store, fast_limiter, transport_for, openalex_work
) -> None:
    budget = CreditBudget(daily_limit=1000, name="openalex", reserve=0)
    transport = transport_for({"/works": {"results": [openalex_work], **openalex_work}})
    provider = OpenAlexProvider(
        api_key=FAKE_KEY,
        mailto=FAKE_MAILTO,
        cache=cache,
        store=store,
        limiter=fast_limiter,
        budget=budget,
        client=httpx.AsyncClient(transport=transport),
    )
    (record,) = await provider.search_works("open access")
    assert budget.used == OpenAlexCost.LIST

    await provider.get_abstract(record.source_id)
    assert budget.used == OpenAlexCost.LIST + OpenAlexCost.SINGLETON


async def test_vector_search_is_off_by_default(oa) -> None:
    provider, transport = oa({})
    with pytest.raises(ConfigurationError) as exc:
        await provider.semantic_search("anything")
    assert "1000 credits" in str(exc.value)
    assert transport.requests == [], "a disabled endpoint must not reach the wire"


async def test_vector_search_refuses_to_spend_the_reserve(
    cache, store, fast_limiter, transport_for
) -> None:
    budget = CreditBudget(daily_limit=100_000, name="openalex", reserve=2_000)
    budget.charge(99_000)
    provider = OpenAlexProvider(
        api_key=FAKE_KEY,
        mailto=FAKE_MAILTO,
        cache=cache,
        store=store,
        limiter=fast_limiter,
        budget=budget,
        enable_vector_search=True,
        vector_endpoint="/works/semantic",
        client=httpx.AsyncClient(transport=transport_for({})),
    )
    with pytest.raises(ConfigurationError) as exc:
        await provider.semantic_search("anything")
    assert "reserve" in str(exc.value)


# --------------------------------------------------------------------------- graph expansion


async def test_citing_and_referenced_use_the_right_filters(oa, openalex_work) -> None:
    """Swapping `cites:` and `cited_by:` yields plausible, entirely wrong candidates."""
    provider, transport = oa({"/works": {"results": [openalex_work]}})

    await provider.citing_works(["https://openalex.org/W111", "W222"])
    assert transport.requests[-1].url.params["filter"] == "cites:W111|W222"

    await provider.referenced_works(["W333"])
    assert transport.requests[-1].url.params["filter"] == "cited_by:W333"


async def test_expansion_ors_fifty_ids_into_one_query(oa, openalex_work) -> None:
    """Fifty seeds is one 10-credit query, not fifty. That is the whole cost argument."""
    provider, transport = oa({"/works": {"results": [openalex_work]}})
    seeds = [f"W{i}" for i in range(MAX_OR_VALUES + 5)]
    await provider.citing_works(seeds)
    assert len(transport.requests) == 2
    assert len(transport.requests[0].url.params["filter"].split("|")) == MAX_OR_VALUES


async def test_batch_hydrate_groups_by_id_kind(oa, openalex_work) -> None:
    provider, transport = oa({"/works": {"results": [openalex_work]}})
    await provider.batch_hydrate(["W1", "10.1/a", "W2"])
    filters = sorted(r.url.params["filter"] for r in transport.requests)
    assert filters == ["doi:10.1/a", "openalex_id:W1|W2"]


async def test_filter_syntax_characters_are_stripped_from_a_title(oa, openalex_work) -> None:
    """A comma in a title is an AND clause; a pipe is an OR. Both silently change the query."""
    provider, transport = oa({"/works": {"results": [openalex_work]}})
    await provider.match_reference("Attention, Memory | Recall: A Study")
    assert transport.requests[-1].url.params["filter"] == "title.search:Attention Memory Recall A Study"


# --------------------------------------------------------------------------- Crossref


async def test_crossref_resolves_a_doi_and_flattens_array_fields(crossref, store) -> None:
    provider, _ = crossref(
        {
            "/works/10.7717/peerj.4375": {
                "message": {
                    "DOI": "10.7717/peerj.4375",
                    "type": "journal-article",
                    "title": ["The state of OA"],
                    "container-title": ["PeerJ"],
                    "author": [{"given": "Heather", "family": "Piwowar"}],
                    "issued": {"date-parts": [[2018, 2, 13]]},
                    "page": "e4375",
                    "URL": "https://doi.org/10.7717/peerj.4375",
                }
            }
        }
    )
    record = await provider.resolve_doi("https://doi.org/10.7717/peerj.4375")
    assert record is not None
    # Arrays flattened — a citeproc handed a list here renders it wrongly.
    assert record.csl["title"] == "The state of OA"
    assert record.csl["container-title"] == "PeerJ"
    assert record.csl["author"] == [{"family": "Piwowar", "given": "Heather"}]
    assert store.has(record.source_id)


async def test_a_missing_doi_is_none_not_an_error(crossref) -> None:
    provider, _ = crossref(handler=lambda r: httpx.Response(404, json={}))
    assert await provider.resolve_doi("10.9999/does-not-exist") is None


async def test_crossref_reports_it_supplies_no_abstracts(crossref) -> None:
    """Accurate rather than convenient: Crossref is not a step in the ADR-006 chain."""
    provider, _ = crossref({})
    assert await provider.get_abstract("src_anything") == (None, AbstractSource.UNAVAILABLE)


# --------------------------------------------------------------------------- fallback chain


class _StubProvider:
    """Stands in for either end of the chain.

    `search_pool_available` is what the resolver reads to decide whether S2's two steps
    can run at all; it is True here so these tests cover the full four-step chain. The
    unauthenticated shape is covered separately, below.
    """

    def __init__(
        self, result: tuple[str | None, AbstractSource], search_pool_available: bool = True
    ) -> None:
        self.result = result
        self.calls = 0
        self.search_pool_available = search_pool_available

    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]:
        self.calls += 1
        return self.result


def _record(store_record):
    return store_record


async def test_chain_prefers_s2_then_openalex_then_tldr(oa, store, openalex_work) -> None:
    provider, _ = oa({"/works": {"results": [openalex_work]}})
    (record,) = await provider.search_works("open access")

    s2_hit = _StubProvider(("Full S2 abstract.", AbstractSource.S2))
    oa_hit = _StubProvider(("Inverted abstract.", AbstractSource.OPENALEX_INVERTED))
    result = await AbstractResolver(semantic_scholar=s2_hit, openalex=oa_hit).resolve(
        record.model_copy(update={"abstract": None, "abstract_source": AbstractSource.UNAVAILABLE})
    )
    assert result.source is AbstractSource.S2
    assert oa_hit.calls == 0, "OpenAlex must not be called once S2 answered"


async def test_openalex_full_abstract_outranks_an_s2_tldr(oa, openalex_work) -> None:
    """A one-sentence generated summary is thinner evidence than a full abstract."""
    provider, _ = oa({"/works": {"results": [openalex_work]}})
    (record,) = await provider.search_works("open access")

    resolver = AbstractResolver(
        semantic_scholar=_StubProvider(("A short summary.", AbstractSource.TLDR)),
        openalex=_StubProvider(("Full inverted abstract.", AbstractSource.OPENALEX_INVERTED)),
    )
    result = await resolver.resolve(
        record.model_copy(update={"abstract": None, "abstract_source": AbstractSource.UNAVAILABLE})
    )
    assert result.source is AbstractSource.OPENALEX_INVERTED


async def test_tldr_is_used_only_when_openalex_has_nothing(oa, openalex_work) -> None:
    provider, _ = oa({"/works": {"results": [openalex_work]}})
    (record,) = await provider.search_works("open access")

    resolver = AbstractResolver(
        semantic_scholar=_StubProvider(("A short summary.", AbstractSource.TLDR)),
        openalex=_StubProvider((None, AbstractSource.UNAVAILABLE)),
    )
    result = await resolver.resolve(
        record.model_copy(update={"abstract": None, "abstract_source": AbstractSource.UNAVAILABLE})
    )
    assert result.source is AbstractSource.TLDR
    assert result.text == "A short summary."


async def test_the_chain_ends_in_a_real_unavailable(oa, openalex_work) -> None:
    """The fourth outcome, displayed rather than skipped (ADR-006)."""
    provider, _ = oa({"/works": {"results": [openalex_work]}})
    (record,) = await provider.search_works("open access")

    resolver = AbstractResolver(
        semantic_scholar=_StubProvider((None, AbstractSource.UNAVAILABLE)),
        openalex=_StubProvider((None, AbstractSource.UNAVAILABLE)),
    )
    result = await resolver.resolve(
        record.model_copy(update={"abstract": None, "abstract_source": AbstractSource.UNAVAILABLE})
    )
    assert result.available is False
    assert result.source is AbstractSource.UNAVAILABLE
    # Which steps were tried is part of saying "we don't know" legibly (HR-3).
    assert result.attempted == (
        AbstractSource.S2,
        AbstractSource.OPENALEX_INVERTED,
        AbstractSource.TLDR,
    )
    assert resolver.unavailable_rate == 1.0


async def test_a_rate_limit_mid_chain_propagates(oa, openalex_work) -> None:
    """A throttled provider must never turn into "this record has no abstract"."""
    from app.core.contracts import ProviderRateLimited

    provider, _ = oa({"/works": {"results": [openalex_work]}})
    (record,) = await provider.search_works("open access")

    class _Throttled:
        # Available, and throttled anyway — which is the case this test is about. A
        # provider that declared itself unavailable would be skipped rather than
        # attempted, and the propagation under test would never get a chance to happen.
        search_pool_available = True

        async def get_abstract(self, source_id: str):
            raise ProviderRateLimited("s2 throttled")

    resolver = AbstractResolver(
        semantic_scholar=_Throttled(),
        openalex=_StubProvider((None, AbstractSource.UNAVAILABLE)),
    )
    with pytest.raises(ProviderRateLimited):
        await resolver.resolve(
            record.model_copy(
                update={"abstract": None, "abstract_source": AbstractSource.UNAVAILABLE}
            )
        )


async def test_an_unauthenticated_s2_is_skipped_so_openalex_can_answer(
    oa, openalex_work
) -> None:
    """The regression that made an optional key behave like a required one.

    `/paper/{id}` is on S2's search pool, so unauthenticated it raises. Step 1 raising
    propagated past step 2 and killed the resolve — losing OpenAlex, the step that would
    have answered, to protect an honesty property that was never at risk. The step is now
    skipped, and the chain returns real evidence.
    """
    provider, _ = oa({"/works": {"results": [openalex_work]}})
    (record,) = await provider.search_works("open access")

    s2 = _StubProvider((None, AbstractSource.UNAVAILABLE), search_pool_available=False)
    resolver = AbstractResolver(
        semantic_scholar=s2,
        openalex=_StubProvider(("Inverted abstract.", AbstractSource.OPENALEX_INVERTED)),
    )

    result = await resolver.resolve(
        record.model_copy(update={"abstract": None, "abstract_source": AbstractSource.UNAVAILABLE})
    )

    assert result.available is True
    assert result.source is AbstractSource.OPENALEX_INVERTED
    assert s2.calls == 0, "a closed endpoint must not be called"


async def test_a_skipped_step_is_declared_not_counted_as_attempted(oa, openalex_work) -> None:
    """"We never asked" and "we asked and got nothing" are different, and both are said.

    A chain shortened by a missing key and one shortened by a record S2 has no abstract
    for both end in `unavailable`; only the first is a configuration the operator can fix,
    so the two must not read the same (HR-3).
    """
    provider, _ = oa({"/works": {"results": [openalex_work]}})
    (record,) = await provider.search_works("open access")

    resolver = AbstractResolver(
        semantic_scholar=_StubProvider(
            (None, AbstractSource.UNAVAILABLE), search_pool_available=False
        ),
        openalex=_StubProvider((None, AbstractSource.UNAVAILABLE)),
    )

    result = await resolver.resolve(
        record.model_copy(update={"abstract": None, "abstract_source": AbstractSource.UNAVAILABLE})
    )

    assert result.source is AbstractSource.UNAVAILABLE
    assert result.attempted == (AbstractSource.OPENALEX_INVERTED,)
    assert result.skipped == (AbstractSource.S2, AbstractSource.TLDR)


async def test_a_stored_tldr_survives_an_unauthenticated_chain(oa, openalex_work) -> None:
    """It came from a response that did reach S2, so it needs no live call to reuse.

    Half of step 3 is a lookup and half is a value already on the record. The gate closes
    the first and leaves the second, which is the difference between "S2 is unreachable"
    and "everything S2 ever told us is void".
    """
    provider, _ = oa({"/works": {"results": [openalex_work]}})
    (record,) = await provider.search_works("open access")

    resolver = AbstractResolver(
        semantic_scholar=_StubProvider(
            (None, AbstractSource.UNAVAILABLE), search_pool_available=False
        ),
        openalex=_StubProvider((None, AbstractSource.UNAVAILABLE)),
    )

    result = await resolver.resolve(
        record.model_copy(
            update={"abstract": "A stored one-liner.", "abstract_source": AbstractSource.TLDR}
        )
    )

    assert result.source is AbstractSource.TLDR
    assert result.text == "A stored one-liner."
