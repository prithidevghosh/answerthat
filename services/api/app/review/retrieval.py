"""`RetrievalService` — the port B3's `AddCitations` and `FindSupport` call.

Returns `source_id`s that a provider adapter has **already written** to the append-only
store. B3 never mints one; by the time an id reaches the planner it is a foreign key into
a record produced by a real HTTP response (HR-1).

One thing worth knowing before you call it. Two of the four retrieval strategies are
seeded from the paper's own bibliography — Recommendations needs S2 ids to sit in
SPECTER2 space, and graph expansion needs OpenAlex ids to hop from. Without a document
those two contribute nothing, so a bare `find_candidates(claim)` runs on **two** of four
strategies rather than four. That is a real reduction in coverage, so it is reported
rather than hidden: pass `doc_id=` (or call `prime(doc_id)` once) to get the full set,
and read `last_strategies` to see which ones actually ran.

A second reduction stacks on that one: `s2_snippet` needs a Semantic Scholar key, and
without one it does not run at all (see `SEARCH_POOL_ENDPOINTS`). So the full set on an
unauthenticated deployment is three, not four. `last_strategies` is the single place to
read what happened — it is asked of the generator rather than reconstructed here, so it
cannot drift from what the generator actually did.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.contracts import Claim
from app.review.candidates import CandidateGenerator, DocumentContext
from app.review.composition import get_providers

__all__ = ["RetrievalService", "get_retrieval_service"]


class RetrievalService:
    def __init__(self, *, providers: Any, document_store: Any = None) -> None:
        self.providers = providers
        self.documents = document_store
        self.generator = CandidateGenerator(
            semantic_scholar=providers.semantic_scholar, openalex=providers.openalex
        )
        self._contexts: dict[str, DocumentContext] = {}
        #: Which strategies were seeded on the most recent call. `("s2_snippet",
        #: "openalex_search")` means the bibliography-seeded two did not run.
        self.last_strategies: tuple[str, ...] = ()

    async def prime(self, doc_id: str) -> DocumentContext:
        """Load and cache a document's bibliography context. Idempotent."""
        cached = self._contexts.get(doc_id)
        if cached is not None:
            return cached

        cited: list = []
        if self.documents is not None:
            document = await self.documents.get(doc_id)
            if document is not None:
                source_ids = sorted(
                    {
                        source_id
                        for section in document.sections
                        for block in section.blocks
                        for span in block.spans
                        for anchor in span.citation_anchors
                        for source_id in anchor.source_ids
                    }
                )
                if source_ids:
                    await self.providers.store.warm(source_ids)
                    cited = [
                        record
                        for record in (self.providers.store.get(sid) for sid in source_ids)
                        if record is not None
                    ]

        context = DocumentContext(cited)
        await self.generator.prepare(context)
        self._contexts[doc_id] = context
        return context

    async def find_candidates(
        self, claim: Claim, limit: int = 10, *, doc_id: str | None = None
    ) -> list[str]:
        """Ranked `source_id`s for this claim, minus everything the paper already cites."""
        context = await self.prime(doc_id) if doc_id else DocumentContext([])
        # Asked, not restated. This list used to be spelled out here from
        # `has_bibliography` alone, which was a second copy of the generator's rule and
        # went wrong the moment a strategy could be dropped for a reason this end did not
        # know about — an unauthenticated `s2_snippet` was reported as having run.
        self.last_strategies = self.generator.strategies_for(context)
        candidates = await self.generator.generate(claim, context)
        return [candidate.source_id for candidate in candidates[:limit]]

    def snippet_for(self, source_id: str) -> str | None:
        return self.generator.snippet_for(source_id)


_service: RetrievalService | None = None


def get_retrieval_service(
    settings: Settings | None = None, *, document_store: Any = None
) -> RetrievalService:
    """Process-wide retrieval service, sharing the one provider bundle and its limiters."""
    global _service
    if _service is None:
        _service = RetrievalService(
            providers=get_providers(settings), document_store=document_store
        )
    elif document_store is not None and _service.documents is None:
        # The composition root may learn about B1's store after the first call.
        _service.documents = document_store
    return _service


def reset() -> None:
    """Tests only."""
    global _service
    _service = None
