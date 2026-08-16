"""Ports — everything B3 needs from another agent's domain, expressed as a Protocol.

B3 never imports from `app/parsing/`, `app/review/`, `app/providers/`, `app/ir/` or
`app/export/`. It depends on these structural types instead, and the composition root in
`app/api/deps.py` binds real implementations. Every port here has a matching Interface
Request in `memory.md` §5.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.contracts import (
    AbstractSource,
    Claim,
    Document,
    SourceRecord,
    Verification,
)

# --------------------------------------------------------------------------- B1 · IR store


@runtime_checkable
class DocumentStore(Protocol):
    """B1's copy-on-write IR store. Versions are immutable once written."""

    async def get(self, doc_id: str, version: int | None = None) -> Document | None:
        """Latest version when `version` is None."""
        ...

    async def put_version(self, document: Document, *, parent_version: int) -> Document:
        """Append a new version. Returns the stored document with its assigned version."""
        ...

    async def list_versions(self, doc_id: str) -> list[int]: ...


# --------------------------------------------------------------------------- B1 · export


@runtime_checkable
class RenderProbe(Protocol):
    """B1's Pandoc renderer, used by the kernel for REJECT rule 5.

    Pure in the sense that matters here: no model call, deterministic for a given
    document. It must never raise to signal "won't render" — it returns the reason so the
    kernel can hand it back to the planner (HR-3).
    """

    def can_render(self, document: Document) -> tuple[bool, str | None]:
        """(True, None) if Pandoc renders the document; (False, reason) if it refuses."""
        ...


@runtime_checkable
class Exporter(Protocol):
    async def to_latex(self, document: Document) -> str: ...


# --------------------------------------------------------------------------- B2 · review


@runtime_checkable
class RetrievalService(Protocol):
    """B2's candidate retrieval. Returns `source_id`s that B2's adapters already wrote to
    the append-only source_store — B3 never mints one (HR-1)."""

    async def find_candidates(self, claim: Claim, limit: int = 10) -> list[str]: ...


@runtime_checkable
class VerificationService(Protocol):
    """B2's quote-backed verifier (ADR-006). Serves both review and edit."""

    async def verify(self, claim_text: str, source_id: str) -> Verification: ...


@runtime_checkable
class ClaimExtractor(Protocol):
    async def extract(self, document: Document, target_ids: list[str]) -> list[Claim]: ...


@runtime_checkable
class ReviewRunner(Protocol):
    """B2's streaming review generator (ADR-014). Yields `(event_name, payload)`."""

    def stream(self, doc_id: str, *, section_ids: list[str] | None = None): ...


# --------------------------------------------------------------------------- B2 · sources


@runtime_checkable
class SourceReader(Protocol):
    """Read-only view of the append-only source_store. B3 gets `get`/`has` only —
    there is deliberately no `put` here, so HR-1 is unviolatable by construction from
    inside `app/agent/`.

    `get`/`has` are synchronous because Appendix A says so, and they answer from an
    in-process index. `warm` fills that index and **must be awaited for every id before it
    is checked** — an unwarmed id raises rather than reporting absence, since "we never
    looked" presented as "does not exist" would be a false kernel REJECT with no way to
    tell it from a real one (memory.md §5, B2 → B3).
    """

    async def warm(self, source_ids: list[str]) -> None: ...
    def get(self, source_id: str) -> SourceRecord | None: ...
    def has(self, source_id: str) -> bool: ...


# --------------------------------------------------------------------------- model ports


@runtime_checkable
class FingerprintStore(Protocol):
    """B1's `anchor_fingerprints` side table (ADR-017).

    An anchor carries a `fingerprint_id`; the 512-float vector lives here. Never inline in
    the IR, for the reason that outranks storage: **the structural diff is how a user
    verifies HR-5 with their own eyes**, and a diff in which every anchor renders as 512
    floats is unreadable.

    Rows are immutable and shared across versions. Re-embedding a sentence writes a new
    row with a new id rather than overwriting an old one, so a fingerprint referenced by an
    older version can never change under it.
    """

    async def put(self, *, vector: list[float], text: str) -> str:
        """Store one vector with the sentence it came from. Returns its `fingerprint_id`."""
        ...

    async def get_many(self, fingerprint_ids: list[str]) -> dict[str, list[float]]:
        """`fingerprint_id → vector`, omitting ids the table does not hold."""
        ...


# --------------------------------------------------------------------------- model ports


@runtime_checkable
class Embedder(Protocol):
    """Sentence embeddings for anchor context fingerprints (ADR-013, ADR-016).

    Backed by `LLMClient.embed()` in production — B3 never constructs an OpenAI client of
    its own, because per-role routing, the token budget and record/replay all live in
    `app/core/llm.py` and none of them survive a second call site (ADR-015, ADR-018).
    """

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class TextModel(Protocol):
    """Prose rewriting only, role `TRANSFORM`. Never sees a citation marker (ADR-013 step 2)."""

    async def rewrite(self, *, system: str, instruction: str, text: str) -> str: ...


@runtime_checkable
class StructuredModel(Protocol):
    """Planner backend, role `PLAN`. Structured output only — it cannot return prose."""

    async def plan(self, *, system: str, prompt: str, schema: dict) -> dict: ...


__all__ = [
    "AbstractSource",
    "ClaimExtractor",
    "DocumentStore",
    "Embedder",
    "Exporter",
    "FingerprintStore",
    "RenderProbe",
    "RetrievalService",
    "ReviewRunner",
    "SourceReader",
    "StructuredModel",
    "TextModel",
    "VerificationService",
]
