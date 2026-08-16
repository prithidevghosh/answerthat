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
                       └──► ReviewLLM ──► app/core/llm.py  (the one OpenAI client)

There is no model SDK anywhere under `app/review/`. Per-role routing (ADR-015),
record/replay (ADR-018) and the per-document token budget all live in `app/core/llm.py`,
and a second client would bypass all three — including in CI, which runs with zero live
calls and would start making them.

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
from app.review.candidates import CandidateGenerator
from app.review.claims import ClaimExtractor
from app.review.llm import ReviewLLM
from app.review.prefilter import EmbeddingPrefilter
from app.review.rerank import Reranker
from app.review.stream import ReviewRunner
from app.review.verifier import Verifier

__all__ = [
    "ProviderBundle",
    "build_review_runner",
    "get_providers",
    "get_llm",
    "get_verifier",
    "get_reranker",
    "get_extractor",
    "get_prefilter",
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
_llm: ReviewLLM | None = None


def get_providers(settings: Settings | None = None) -> ProviderBundle:
    """The one provider bundle for this process. Shares one limiter per provider."""
    global _providers
    if _providers is None:
        _providers = ProviderBundle(settings or get_settings())
    return _providers


def get_llm(settings: Settings | None = None) -> ReviewLLM:
    """The review pipeline's view of the one shared LLM client.

    Not a client of its own: it delegates to `app/core/llm.py`, where the model for each
    role is chosen, recordings are read and written, and the token budget is charged.
    `OPENAI_API_KEY` is already required at startup by `app/core/config.py` (HR-2), so
    there is no key check here and no offline review mode to fall back to.
    """
    global _llm
    if _llm is None:
        _llm = ReviewLLM(settings=settings or get_settings())
    return _llm


def get_verifier(settings: Settings | None = None) -> Verifier:
    """The `VERIFY` stage. One verifier, two callers (ADR-006)."""
    providers = get_providers(settings)
    return Verifier(
        llm=get_llm(settings), abstracts=providers.abstracts, source_store=providers.store
    )


def get_reranker(settings: Settings | None = None) -> Reranker:
    """The `RERANK` stage. Keeps `VERIFY_KEEP` candidates per claim (ADR-024)."""
    resolved = settings or get_settings()
    return Reranker(llm=get_llm(resolved), keep_top=resolved.verify_keep)


def get_prefilter(settings: Settings | None = None) -> EmbeddingPrefilter:
    """The free stage in front of both. Cuts to `RERANK_KEEP` with embeddings (ADR-016)."""
    resolved = settings or get_settings()
    return EmbeddingPrefilter(llm=get_llm(resolved), keep=resolved.rerank_keep)


def get_extractor(settings: Settings | None = None) -> ClaimExtractor:
    """The `CLAIM_EXTRACTION` stage. Drops claims below `CITABILITY_MIN` (ADR-024)."""
    resolved = settings or get_settings()
    return ClaimExtractor(llm=get_llm(resolved), min_citability=resolved.citability_min)


def build_review_runner(document_store: Any, settings: Settings | None = None) -> ReviewRunner:
    """The whole streaming pipeline, assembled. The factory `app/api/` binds (IR-5).

    B3's composition root owns B1's `DocumentStore` and `app/review/` must not import it,
    so the runner cannot be constructed from either side alone. This is B2's half: pass
    the document store in, get a runner out. `get_review_runner(factory=...)` in
    `runner.py` takes a zero-argument callable, so bind it as
    `lambda: build_review_runner(document_store)`.

    **The cascade is wired here, in one place.** Extraction below `CITABILITY_MIN` is
    dropped, the embedding prefilter cuts to `RERANK_KEEP`, the reranker cuts to
    `VERIFY_KEEP`, and only those reach the verifier. Assembling a runner without the
    prefilter is legal — `stream.py` treats it as optional — and would produce identical
    findings at several times the cost per claim, which is the pressure that eventually
    gets answered by economising on verification instead. So production builds it here
    rather than leaving each caller to remember.
    """
    resolved = settings or get_settings()
    providers = get_providers(resolved)
    return ReviewRunner(
        document_store=document_store,
        source_store=providers.store,
        extractor=get_extractor(resolved),
        candidates=CandidateGenerator(
            semantic_scholar=providers.semantic_scholar, openalex=providers.openalex
        ),
        prefilter=get_prefilter(resolved),
        reranker=get_reranker(resolved),
        verifier=get_verifier(resolved),
    )


def install(*, providers: ProviderBundle | None = None, llm: ReviewLLM | None = None) -> None:
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
