"""HR-1 — a source can only enter the store from a real HTTP response, via a provider.

Every test here is an attack. If one of them passes when it should fail, an LLM somewhere
in this system can mint a citation, and every downstream guarantee — the kernel's REJECT
rule 1, clickable findings, the honesty audit — is decorative.

Note the shape of the positive path: this module *cannot* write a source, by design. The
successful-write tests live in `test_adapters_*.py`, where the write goes through a real
adapter against a mocked transport. That asymmetry is the point.
"""

from __future__ import annotations

import pytest

from app.core.contracts import AbstractSource, Provenance, SourceRecord
from app.core.errors import SourceStoreViolation
from app.providers.errors import AppendOnlyViolation, UnprovenanceredSource
from app.providers.http import PROVENANCE_REGISTRY, ProviderResponse
from app.providers.source_store import (
    InMemorySourceStore,
    SourceNotIndexed,
    _merge_append_only,
)


def _real_response(endpoint: str = "/paper/search") -> ProviderResponse:
    """A response object of the kind `ProviderHTTP` returns after a real call."""
    return ProviderResponse(
        provider="semantic_scholar",
        endpoint=endpoint,
        url="https://api.semanticscholar.org/graph/v1" + endpoint,
        body={},
        retrieved_at="2026-08-15T10:00:00+00:00",
        from_cache=False,
    )


def _record(provenance: Provenance, **overrides) -> SourceRecord:
    base = {
        "source_id": "src_test0001",
        "csl": {"title": "Attention Is All You Need", "type": "paper-conference"},
        "provenance": provenance,
        "abstract": None,
        "abstract_source": AbstractSource.UNAVAILABLE,
    }
    base.update(overrides)
    return SourceRecord(**base)


# --------------------------------------------------------------- guard 1: caller module


async def test_a_non_provider_module_cannot_write() -> None:
    """This test module is not `app.providers.*`, so the store must refuse it."""
    store = InMemorySourceStore()
    provenance = _real_response().provenance("https://doi.org/10.5555/real")
    with pytest.raises(SourceStoreViolation) as exc:
        await store.put(_record(provenance))
    assert "Only app.providers.* may write" in str(exc.value)
    assert store.writes == 0


# --------------------------------------------------------------- guard 2: real provenance


async def test_a_hand_built_provenance_is_refused() -> None:
    """The exact shape of a fabricated citation: plausible metadata, no HTTP response.

    A model that emitted this dict would have every field right and still be inventing
    the source. Refused because no response minted it.
    """
    fabricated = Provenance(
        provider="semantic_scholar",
        endpoint="/paper/search",
        retrieved_at="2026-08-15T10:00:00+00:00",
        external_url="https://doi.org/10.1234/plausible-but-invented",
    )
    assert not PROVENANCE_REGISTRY.contains(fabricated)

    from app.providers import source_store as ss

    with pytest.raises(UnprovenanceredSource):
        ss._assert_real_provenance(_record(fabricated))


async def test_a_minted_provenance_is_accepted_by_the_provenance_guard() -> None:
    from app.providers import source_store as ss

    provenance = _real_response().provenance("https://doi.org/10.5555/real")
    assert PROVENANCE_REGISTRY.contains(provenance)
    ss._assert_real_provenance(_record(provenance))  # does not raise


# --------------------------------------------------------------- guard 3: resolvable URL


@pytest.mark.parametrize(
    "url",
    ["", "not-a-url", "/works/W123", "javascript:alert(1)", "doi:10.1234/x", "file:///etc/passwd"],
)
def test_an_unopenable_external_url_is_refused(url: str) -> None:
    """A finding a reader cannot open is indistinguishable from a fabricated one."""
    from app.providers import source_store as ss

    # Mint through the real path so only the URL check can fail.
    provenance = _real_response().provenance(url)
    with pytest.raises(SourceStoreViolation) as exc:
        ss._assert_real_provenance(_record(provenance))
    assert "absolute http(s) URL" in str(exc.value)


# --------------------------------------------------------------- guard 4: append-only


def _stored(**overrides) -> SourceRecord:
    return _record(_real_response().provenance("https://doi.org/10.5555/real"), **overrides)


def test_a_later_abstract_enriches_rather_than_overwrites() -> None:
    """The fallback chain legitimately fills in an abstract after the first write."""
    existing = _stored()
    incoming = _stored(abstract="We propose a new architecture.", abstract_source=AbstractSource.S2)
    merged = _merge_append_only(existing, incoming)
    assert merged is not None
    assert merged.abstract == "We propose a new architecture."
    assert merged.abstract_source is AbstractSource.S2
    assert merged.csl == existing.csl


def test_new_csl_fields_are_added_but_existing_ones_are_never_changed() -> None:
    existing = _stored(csl={"title": "T", "DOI": "10.5555/real"})
    incoming = _stored(csl={"title": "T", "DOI": "10.5555/real", "issued": {"date-parts": [[2017]]}})
    merged = _merge_append_only(existing, incoming)
    assert merged is not None
    assert merged.csl["issued"] == {"date-parts": [[2017]]}


def test_changing_a_stored_value_is_refused() -> None:
    existing = _stored(csl={"title": "Attention Is All You Need"})
    incoming = _stored(csl={"title": "Attention Is Mostly What You Need"})
    with pytest.raises(AppendOnlyViolation) as exc:
        _merge_append_only(existing, incoming)
    assert "csl.title" in str(exc.value)


def test_two_providers_disagreeing_on_an_abstract_surfaces_rather_than_picks() -> None:
    """A quote may already have been substring-checked against the stored abstract."""
    existing = _stored(abstract="Original abstract text.", abstract_source=AbstractSource.S2)
    incoming = _stored(
        abstract="A different abstract text.", abstract_source=AbstractSource.OPENALEX_INVERTED
    )
    with pytest.raises(AppendOnlyViolation):
        _merge_append_only(existing, incoming)


def test_an_identical_rewrite_is_a_no_op_not_a_new_version() -> None:
    existing = _stored()
    assert _merge_append_only(existing, _stored()) is None


def test_an_unavailable_abstract_never_clobbers_a_real_one() -> None:
    existing = _stored(abstract="Real abstract.", abstract_source=AbstractSource.S2)
    incoming = _stored(abstract=None, abstract_source=AbstractSource.UNAVAILABLE)
    assert _merge_append_only(existing, incoming) is None


# --------------------------------------------------------------- sync reads (HR-3)


async def test_sync_has_raises_for_an_id_we_never_looked_up() -> None:
    """"We never looked" must not be reported as "it does not exist".

    Returning False here would produce a *correct-looking* kernel REJECT for a real
    source, and the operator would have no way to tell it from a fabricated one.
    """
    store = InMemorySourceStore()
    with pytest.raises(SourceNotIndexed):
        store.has("src_never_seen")
    with pytest.raises(SourceNotIndexed):
        store.get("src_never_seen")


async def test_warming_an_absent_id_lets_has_answer_false() -> None:
    """This is the path the kernel uses to reject a fabricated `source_id`."""
    store = InMemorySourceStore()
    await store.warm(["src_fabricated_by_a_model"])
    assert store.has("src_fabricated_by_a_model") is False
    assert store.get("src_fabricated_by_a_model") is None
