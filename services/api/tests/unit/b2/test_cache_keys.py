"""Cache key derivation and the in-memory cache.

The two properties worth pinning: credentials never enter a key, and semantically equal
requests share one entry. The second is what makes re-review nearly free, and the demo
reproducible, against a 1 rps provider.
"""

from __future__ import annotations

from app.providers.cache import InMemoryResponseCache, cache_key, normalize_query


def test_equal_requests_share_one_entry_regardless_of_spelling() -> None:
    a, hash_a = normalize_query({"query": "Attention  Is All You Need", "limit": 10})
    b, hash_b = normalize_query({"limit": 10, "query": "attention is all you need"})
    assert hash_a == hash_b
    assert a == b


def test_none_valued_parameters_are_dropped() -> None:
    _, with_none = normalize_query({"query": "x", "year": None})
    _, without = normalize_query({"query": "x"})
    assert with_none == without


def test_list_order_does_not_fragment_the_cache() -> None:
    _, a = normalize_query({"ids": ["W3", "W1", "W2"]})
    _, b = normalize_query({"ids": ["W1", "W2", "W3"]})
    assert a == b


def test_distinct_requests_get_distinct_keys() -> None:
    _, a = normalize_query({"query": "graph neural networks"})
    _, b = normalize_query({"query": "graph neural network"})
    assert a != b


def test_key_is_scoped_by_provider_and_endpoint() -> None:
    _, qh = normalize_query({"query": "x"})
    assert cache_key("openalex", "/works", qh) != cache_key("semantic_scholar", "/works", qh)
    assert cache_key("openalex", "/works", qh) != cache_key("openalex", "/authors", qh)


async def test_in_memory_cache_round_trip_and_miss() -> None:
    cache = InMemoryResponseCache()
    assert await cache.get("openalex", "/works", "h1") is None
    await cache.put("openalex", "/works", "h1", "{}", {"body": {"ok": True}}, ttl_s=60)
    hit = await cache.get("openalex", "/works", "h1")
    assert hit is not None and hit.payload["body"] == {"ok": True}
    assert cache.hits == 1 and cache.misses == 1


async def test_expired_entries_are_a_miss_not_a_stale_hit() -> None:
    cache = InMemoryResponseCache()
    await cache.put("s2", "/paper/search", "h1", "{}", {"body": {}}, ttl_s=-1)
    assert await cache.get("s2", "/paper/search", "h1") is None
    assert len(cache) == 0
