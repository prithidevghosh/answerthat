"""Composition root for the review services. Answers B3's Interface Request.

Everything here is a process-wide singleton, and that is load-bearing rather than tidy:
**the rate limiter must be shared.** Semantic Scholar's ~1 rps allowance is per key, not
per client, so a second `SemanticScholarProvider` would mean a second `TokenBucket` and
twice the request rate against a limit we would then be silently over. The same argument
applies to the OpenAlex credit budget, which is a daily total rather than a per-client
one.

The wiring order is the dependency order, and each step raises rather than degrading:

    Settings ──► cache ──► S2 / OpenAlex / Crossref adapters   (MissingAPIKeyError, HR-2)
             └──► source_store ──┘
                       │
                       ├──► AbstractResolver  (both providers required, ADR-006)
                       └──► StructuredLLM     (MissingAPIKeyError at point of use)

Call `reset()` between tests, or the singletons leak across them.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings, get_settings
from app.providers.abstracts import AbstractResolver
from app.providers.cache import PostgresResponseCache, ResponseCache
from app.providers.crossref import CrossrefProvider
from app.providers.openalex import OpenAlexProvider
from app.providers.semantic_scholar import SemanticScholarProvider
from app.providers.source_store import PostgresSourceStore
from app.review.claims import ClaimExtractor
from app.review.llm import StructuredLLM
from app.review.rerank import Reranker
from app.review.verifier import Verifier

__all__ = [
    "ProviderBundle",
    "get_providers",
    "get_llm",
    "get_verifier",
    "get_reranker",
    "reset",
]


class ProviderBundle:
    """The three adapters, the store, and the abstract chain, wired once."""

    __slots__ = ("cache", "store", "semantic_scholar", "openalex", "crossref", "abstracts")

    def __init__(
        self,
        settings: Settings,
        *,
        cache: ResponseCache | None = None,
        store: Any = None,
    ) -> None:
        self.cache = cache or PostgresResponseCache()
        self.store = store or PostgresSourceStore()

        # Each constructor raises MissingAPIKeyError if its credential is absent. There
        # is deliberately no try/except around them: a bundle that came up with two of
        # three adapters would produce a review that looks complete (HR-2 / ADR-010).
        self.semantic_scholar = SemanticScholarProvider(
            api_key=settings.semantic_scholar_api_key, cache=self.cache, store=self.store
        )
        self.openalex = OpenAlexProvider(
            api_key=settings.openalex_api_key,
            mailto=settings.openalex_mailto,
            cache=self.cache,
            store=self.store,
        )
        self.crossref = CrossrefProvider(
            mailto=settings.openalex_mailto, cache=self.cache, store=self.store
        )
        self.abstracts = AbstractResolver(
            semantic_scholar=self.semantic_scholar, openalex=self.openalex
        )

    async def aclose(self) -> None:
        await self.semantic_scholar.aclose()
        await self.openalex.aclose()
        await self.crossref.aclose()

    def snapshot(self) -> dict[str, Any]:
        return {
            "semantic_scholar": self.semantic_scholar.snapshot(),
            "openalex": self.openalex.snapshot(),
            "crossref": self.crossref.snapshot(),
        }


_providers: ProviderBundle | None = None
_llm: StructuredLLM | None = None


def get_providers(settings: Settings | None = None) -> ProviderBundle:
    """The one provider bundle for this process. Shares one limiter per provider."""
    global _providers
    if _providers is None:
        _providers = ProviderBundle(settings or get_settings())
    return _providers


def get_llm(settings: Settings | None = None) -> StructuredLLM:
    """The one model client. Raises `MissingAPIKeyError` if `ANTHROPIC_API_KEY` is absent."""
    global _llm
    if _llm is None:
        _llm = StructuredLLM(api_key=(settings or get_settings()).anthropic_api_key)
    return _llm


def get_verifier(settings: Settings | None = None) -> Verifier:
    providers = get_providers(settings)
    return Verifier(
        llm=get_llm(settings), abstracts=providers.abstracts, source_store=providers.store
    )


def get_reranker(settings: Settings | None = None) -> Reranker:
    return Reranker(llm=get_llm(settings))


def get_extractor(settings: Settings | None = None) -> ClaimExtractor:
    return ClaimExtractor(llm=get_llm(settings))


def install(*, providers: ProviderBundle | None = None, llm: StructuredLLM | None = None) -> None:
    """Inject pre-built singletons. Tests and fixture replays only."""
    global _providers, _llm
    if providers is not None:
        _providers = providers
    if llm is not None:
        _llm = llm


def reset() -> None:
    """Drop the singletons. Tests only — production wires once per process."""
    global _providers, _llm
    _providers = None
    _llm = None
