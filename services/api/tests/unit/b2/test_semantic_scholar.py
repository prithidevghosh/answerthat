"""Semantic Scholar adapter, against a mocked transport.

Covers the three things the rest of the system depends on this adapter getting right:
key enforcement at construction, `source_store` writes carrying real provenance, and
batching instead of looping at ~1 rps.
"""

from __future__ import annotations

import inspect

import httpx
import pytest

from app.core.contracts import AbstractSource, MissingAPIKeyError
from app.providers.semantic_scholar import BATCH_LIMIT, SemanticScholarProvider

FAKE_S2_KEY = "test-s2-key"


@pytest.fixture
def s2(cache, store, fast_limiter, transport_for):
    """`s2(routes)` -> `(provider, transport)`."""

    def build(routes=None, *, handler=None):
        transport = transport_for(routes, handler=handler)
        provider = SemanticScholarProvider(
            api_key=FAKE_S2_KEY,
            cache=cache,
            store=store,
            limiter=fast_limiter,
            client=httpx.AsyncClient(transport=transport),
        )
        return provider, transport

    return build


# --------------------------------------------------------------------------- HR-2


@pytest.mark.parametrize("absent", [None, "", "   "])
def test_construction_raises_without_a_key(cache, store, absent) -> None:
    with pytest.raises(MissingAPIKeyError):
        SemanticScholarProvider(api_key=absent, cache=cache, store=store)


def test_there_is_no_anonymous_constructor_argument() -> None:
    """A regression guard on the shape of the constructor, not just its behaviour.

    ADR-010's failure mode arrives as a helpful-looking kwarg (`allow_anonymous=True`,
    `require_key=False`) long before it arrives as a deleted check.
    """
    params = set(inspect.signature(SemanticScholarProvider.__init__).parameters)
    forbidden = {"allow_anonymous", "anonymous", "require_key", "optional_key", "degraded"}
    assert not (params & forbidden), f"anonymous escape hatch appeared: {params & forbidden}"


# --------------------------------------------------------------------------- HR-1 positive path


async def test_search_writes_records_to_the_store_with_real_provenance(
    s2, store, attention_paper
) -> None:
    provider, _ = s2({"/graph/v1/paper/search": {"data": [attention_paper]}})
    records = await provider.search_works("attention transformers", limit=5)

    assert len(records) == 1
    record = records[0]
    # The store is the authority: the caller received an id that already exists there.
    assert store.has(record.source_id) is True
    assert store.writes == 1
    # Provenance names the endpoint and a resolvable URL a reader can open.
    assert record.provenance.provider == "semantic_scholar"
    assert record.provenance.endpoint == "/paper/search"
    assert record.provenance.external_url == "https://doi.org/10.5555/3295222.3295349"
    assert record.csl["title"] == "Attention Is All You Need"
    assert record.csl["author"][0] == {"family": "Vaswani", "given": "Ashish"}
    assert record.abstract_source is AbstractSource.S2


async def test_the_key_is_sent_on_every_request(s2, attention_paper) -> None:
    provider, transport = s2({"/graph/v1/paper/search": {"data": [attention_paper]}})
    await provider.search_works("x")
    assert transport.requests[0].headers["x-api-key"] == FAKE_S2_KEY


async def test_credentials_never_reach_the_cache(s2, cache, attention_paper) -> None:
    provider, _ = s2({"/graph/v1/paper/search": {"data": [attention_paper]}})
    await provider.search_works("x")
    assert len(cache) == 1, "the response should have been cached"
    assert FAKE_S2_KEY not in repr(cache._rows)


async def test_a_repeated_search_is_served_from_cache(s2, attention_paper) -> None:
    """What makes re-review nearly free, and the demo reproducible, at ~1 rps."""
    provider, transport = s2({"/graph/v1/paper/search": {"data": [attention_paper]}})
    await provider.search_works("attention")
    await provider.search_works("ATTENTION")  # normalized to the same key
    assert len(transport.requests) == 1


# --------------------------------------------------------------------------- match / arbiter


async def test_match_reference_returns_a_record(s2, attention_paper) -> None:
    provider, _ = s2(
        {"/graph/v1/paper/search/match": {"data": [dict(attention_paper, matchScore=180.0)]}}
    )
    record = await provider.match_reference("Attention Is All You Need", year=2017)
    assert record is not None
    assert record.csl["DOI"] == "10.5555/3295222.3295349"


async def test_no_title_match_is_a_result_not_a_failure(s2, store) -> None:
    """A 404 from `/paper/search/match` is the arbiter learning "resolves nowhere"."""
    provider, _ = s2(handler=lambda r: httpx.Response(404, json={"error": "Title match not found"}))
    assert await provider.match_reference("A paper that does not exist") is None
    assert store.writes == 0


async def test_a_404_is_cached_so_the_arbiter_does_not_re_spend_the_rate_limit(s2) -> None:
    provider, transport = s2(handler=lambda r: httpx.Response(404, json={}))
    await provider.match_reference("nope")
    await provider.match_reference("nope")
    assert len(transport.requests) == 1


# --------------------------------------------------------------------------- batching


async def test_batch_hydrate_sorts_ids_and_makes_one_call(s2, attention_paper) -> None:
    """At ~1 rps, looping over ids is the difference between 2 seconds and 8 minutes."""
    provider, transport = s2({"/graph/v1/paper/batch": {"data": [attention_paper]}})
    await provider.batch_hydrate(["DOI:10.3/c", "DOI:10.1/a", "DOI:10.2/b"])

    assert len(transport.requests) == 1
    assert transport.last_body()["ids"] == ["DOI:10.1/a", "DOI:10.2/b", "DOI:10.3/c"]


async def test_a_reordered_batch_request_is_a_cache_hit(s2, attention_paper) -> None:
    provider, transport = s2({"/graph/v1/paper/batch": {"data": [attention_paper]}})
    await provider.batch_hydrate(["DOI:10.1/a", "DOI:10.2/b"])
    await provider.batch_hydrate(["DOI:10.2/b", "DOI:10.1/a"])
    assert len(transport.requests) == 1


async def test_nulls_in_a_batch_response_are_dropped_not_errors(s2, attention_paper) -> None:
    provider, _ = s2({"/graph/v1/paper/batch": {"data": [attention_paper, None]}})
    records = await provider.batch_hydrate(["DOI:10.1/a", "DOI:10.9/missing"])
    assert len(records) == 1


async def test_batch_chunks_at_the_api_limit(s2) -> None:
    provider, transport = s2({"/graph/v1/paper/batch": {"data": []}})
    await provider.batch_hydrate([f"DOI:10.1/{i:04d}" for i in range(BATCH_LIMIT + 10)])
    assert len(transport.requests) == 2
    assert len(transport.last_body()["ids"]) == 10


# --------------------------------------------------------------------------- abstracts


async def test_abstract_falls_back_to_tldr_then_reports_unavailable(
    s2, no_abstract_paper
) -> None:
    provider, _ = s2(
        {
            "/graph/v1/paper/search": {"data": [no_abstract_paper]},
            "/graph/v1/paper/abc123": dict(no_abstract_paper, abstract=None, tldr=None),
        }
    )
    (record,) = await provider.search_works("licensing")
    assert record.abstract is None
    assert record.abstract_source is AbstractSource.UNAVAILABLE

    text, source = await provider.get_abstract(record.source_id)
    assert text is None
    assert source is AbstractSource.UNAVAILABLE, "unavailable is an outcome, not an error"


async def test_a_later_abstract_enriches_the_stored_record(s2, store, no_abstract_paper) -> None:
    provider, _ = s2(
        {
            "/graph/v1/paper/search": {"data": [no_abstract_paper]},
            "/graph/v1/paper/abc123": dict(
                no_abstract_paper, abstract=None, tldr={"text": "A short summary."}
            ),
        }
    )
    (record,) = await provider.search_works("licensing")
    text, source = await provider.get_abstract(record.source_id)

    assert text == "A short summary."
    assert source is AbstractSource.TLDR
    # Appended as a new version; the original is still there.
    history = await store.history(record.source_id)
    assert len(history) == 2
    assert history[0].abstract is None
    assert history[1].abstract == "A short summary."


async def test_name_particles_survive_the_split(s2, no_abstract_paper) -> None:
    provider, _ = s2({"/graph/v1/paper/search": {"data": [no_abstract_paper]}})
    (record,) = await provider.search_works("x")
    assert record.csl["author"][0] == {"family": "van der Berg", "given": "Ada"}
    # The provider's original string is retained, so a bad split is auditable.
    assert record.csl["custom"]["raw_author_names"] == ["Ada van der Berg"]


# --------------------------------------------------------------------------- snippets


async def test_snippet_search_returns_evidence_text_linked_to_stored_records(
    s2, store, attention_paper
) -> None:
    """ADR-005: passage-level evidence, not a title that is merely on-topic."""
    provider, _ = s2(
        {
            "/graph/v1/snippet/search": {
                "data": [
                    {
                        "score": 0.87,
                        "snippet": {
                            "text": "self-attention scales quadratically with sequence length",
                            "snippetKind": "body",
                            "section": "Background",
                        },
                        "paper": {"corpusId": 13756489, "title": "Attention Is All You Need"},
                    }
                ]
            },
            "/graph/v1/paper/batch": {"data": [attention_paper]},
        }
    )
    snippets = await provider.snippet_search("attention cost", limit=5)

    assert len(snippets) == 1
    assert "quadratically" in snippets[0].text
    assert store.has(snippets[0].source_id) is True


async def test_a_snippet_that_cannot_be_hydrated_is_dropped_not_invented(s2, store) -> None:
    """No stored record means no source_id — and minting one would be HR-1's failure."""
    provider, _ = s2(
        {
            "/graph/v1/snippet/search": {
                "data": [
                    {
                        "score": 0.9,
                        "snippet": {"text": "some evidence", "snippetKind": "body"},
                        "paper": {"corpusId": 424242, "title": "Unhydratable"},
                    }
                ]
            },
            "/graph/v1/paper/batch": {"data": []},
        }
    )
    assert await provider.snippet_search("x") == []
    assert store.writes == 0


# --------------------------------------------------------------------------- recommendations


async def test_recommendations_are_seeded_with_the_papers_own_cited_works(
    s2, attention_paper
) -> None:
    """ADR-005's genuinely semantic signal: the SPECTER2 neighbourhood of the bibliography."""
    provider, transport = s2({"/recommendations/v1/papers": {"recommendedPapers": [attention_paper]}})
    records = await provider.recommendations_from(["s2id_b", "s2id_a"], limit=10)

    assert len(records) == 1
    assert transport.last_body()["positivePaperIds"] == ["s2id_a", "s2id_b"]


async def test_recommendations_without_seeds_makes_no_call(s2) -> None:
    provider, transport = s2({"/recommendations/v1/papers": {"recommendedPapers": []}})
    assert await provider.recommendations_from([]) == []
    assert transport.requests == []
