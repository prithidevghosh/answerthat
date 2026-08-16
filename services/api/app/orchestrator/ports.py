"""Ports — everything the orchestrator needs from another package, as a Protocol.

`app/orchestrator/` never imports `app/parsing/`, `app/review/`, `app/agent/`,
`app/providers/`, `app/ir/` or `app/export/`. It depends on these structural types
instead, and `app/api/deps.py` binds real implementations. This is the same arrangement
`app/agent/ports.py` established, for the same reason: it is what keeps the packages
separable, and it is the first thing that gets quietly violated.

Two conventions worth stating, because they show up in nearly every signature here.

**Payloads that cross this boundary are plain dicts.** A `ProposedChangeSet` is a
pydantic model in `app/agent/`, and importing it to type a return value would defeat the
purpose of the file. The adapters call `.model_dump(mode="json")` and this package reads
the result structurally — which is also exactly the shape the tool result has to be in to
reach the frontend, so nothing is lost in the translation.

**Sync-or-async is bridged by the caller, not guessed at here.** B1's ingest pipeline and
B2's review runner start background work with `asyncio.create_task` and expose
synchronous entry points; `app.api.adapters.maybe_await` bridges that at the call site.
Methods that may be either are declared returning `Any` and documented as such.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from app.core.contracts import Document, SourceRecord

# --------------------------------------------------------------------------- parsing


@runtime_checkable
class IngestGateway(Protocol):
    """B1's ingest pipeline: progress, the parse report, and the draft IR."""

    def status(self, doc_id: str) -> Any:
        """`{state, stage, progress, version, error, elapsed_s}`, or None if unknown.

        `progress` is the real stage-position fraction `IngestRecord.progress` computes
        from `STAGES`. It is never a timer and never interpolated — a bar that moves on a
        clock is a bar that lies about a stalled parse.
        """
        ...

    async def parse_report(self, doc_id: str, version: int | None = None) -> dict:
        """The full report. Raises `ParseFailure` while the ingest is still running.

        Refusing is the point: a half-report reads as a paper with few references, which
        is the false negative HR-3 is written against.
        """
        ...

    def draft_document(self, doc_id: str) -> Document | None:
        """The IR as `tei_to_ir` built it, before reconciliation (ADR-033).

        None before that stage, which is the honest answer while GROBID is still running.
        """
        ...


@runtime_checkable
class StyleGateway(Protocol):
    """B1's style detection and override. Both may be sync — bridge with `maybe_await`."""

    def detect(self, doc_id: str) -> Any: ...
    def select(self, doc_id: str, style_id: str) -> Any: ...


# --------------------------------------------------------------------------- IR


@runtime_checkable
class DocumentReader(Protocol):
    """B1's versioned IR store, read side only.

    There is no `put_version` here. The orchestrator writes document versions through
    exactly two tools — `commit_change_set` and `revert_document` — and both go through
    `VersionGateway`, which runs the kernel. A general-purpose write port would be a
    second, unchecked path to the same table.
    """

    async def get(self, doc_id: str, version: int | None = None) -> Document | None: ...
    async def list_versions(self, doc_id: str) -> list[int]: ...


# --------------------------------------------------------------------------- sources


@runtime_checkable
class SourceReader(Protocol):
    """Read-only view of the append-only source_store. HR-1.

    There is deliberately no `put`. A `source_id` exists because a provider adapter saw
    it in an HTTP response; nothing in this package can mint one, and the absence of the
    method is what makes that structural rather than a rule someone remembers.

    `get`/`has` are sync and answer from an in-process index. `warm` fills it and **must
    be awaited for every id before it is checked** — an unwarmed id raises rather than
    reporting absence, because "we never looked" presented as "does not exist" is a false
    negative with no way to tell it from a real one.
    """

    async def warm(self, source_ids: list[str]) -> None: ...
    def get(self, source_id: str) -> SourceRecord | None: ...
    def has(self, source_id: str) -> bool: ...


# --------------------------------------------------------------------------- review


@runtime_checkable
class ReviewGateway(Protocol):
    """B2's review job runner, plus read access to what a finished job produced."""

    def start(self, doc_id: str, section_ids: list[str] | None = None, *, force: bool = False) -> Any:
        """Returns the job id. Idempotent: a running or completed review of the same
        scope returns the existing job rather than billing a second pass."""
        ...

    def status(self, doc_id: str) -> Any:
        """Status plus the full `ReviewStats` payload."""
        ...

    def stream(self, doc_id: str) -> AsyncIterator[tuple[str, dict]]:
        """Replay this document's events, then follow live. Raises for a document whose
        review was never started."""
        ...

    def findings(self, doc_id: str) -> list[dict]:
        """Every `finding` event this document's job has emitted, in emission order
        (citability descending). Read from the job's own event log rather than recomputed,
        so the chat and the review feed cannot disagree about what was found."""
        ...


@runtime_checkable
class RetrievalIntrospector(Protocol):
    """What a review of *this* document will actually do, asked of the live system.

    This exists so that `describe_review_plan` reports facts rather than a paragraph
    somebody wrote once. Which strategies run depends on whether `SEMANTIC_SCHOLAR_API_KEY`
    is set and on whether the paper's bibliography resolved to seedable ids — both runtime
    facts, and both invisible in the findings list they change.
    """

    async def strategies_for(self, doc_id: str) -> tuple[list[str], list[str]]:
        """`(will_run, will_not_run)`, both drawn from `CandidateGenerator`'s own rule."""
        ...


# --------------------------------------------------------------------------- editing


@runtime_checkable
class CommandGateway(Protocol):
    """B3's plan → execute → kernel → propose loop, and the store proposals live in."""

    async def propose(self, document: Document, instruction: str) -> dict:
        """Run the command loop and store the resulting change set. Writes no document
        version. Returns the change set as a dict — including, per change, the kernel's
        verdict with its reasons verbatim, the structural diff, and any orphaned anchors
        with the scores they fell short by."""
        ...

    def get_change_set(self, change_set_id: str) -> dict:
        """Raises `ChangeSetNotFound` for one that expired or never existed."""
        ...


@runtime_checkable
class VersionGateway(Protocol):
    """The only path in this package that writes a document version."""

    async def commit(
        self,
        change_set_id: str,
        *,
        base_version: int,
        approved_change_ids: list[str],
        rejected_change_ids: list[str],
        orphan_decisions: list[dict],
    ) -> dict:
        """Commit the approved subset. The kernel runs again here, against the document
        as it actually is. Raises on a moved head (ADR-021) and on an orphaned anchor
        still waiting on a decision (HR-5) — neither is resolved on the user's behalf."""
        ...

    async def revert(self, doc_id: str, to_version: int) -> dict: ...

    async def set_style(self, document: Document) -> int:
        """Commit a document whose `metadata.style_id` the user has chosen; returns the
        new version number.

        Narrow on purpose — it takes a whole `Document` and writes it, which is a general
        write in every respect but intent. It exists because a style choice is a fact
        about the document and the IR store is append-only, so recording it is a version
        (the deterministic screen's `_persist_style` reaches the same conclusion). It is
        not a hole in the "two write paths" rule above so much as a third, deliberately
        boring one: no kernel evaluation is needed because no text and no citation
        changes.
        """
        ...


# --------------------------------------------------------------------------- export


@runtime_checkable
class ExportGateway(Protocol):
    """B1's LaTeX exporter, and the manifest that must be disclosed before it runs."""

    async def manifest(self, doc_id: str, version: int | None = None) -> dict:
        """Placeholder counts, bibliography size, style, `exportable`, `blocked_reason`.
        The ADR-008 placeholder disclosure is in here."""
        ...

    async def to_latex(self, doc_id: str, version: int | None = None) -> dict:
        """`{filename, byte_size, download_url, style_id, style_uncertain}`. Raises
        `ExportFailure` with the exporter's own message when it refuses."""
        ...


# --------------------------------------------------------------------------- models


@runtime_checkable
class ConversationModel(Protocol):
    """`app/core/llm.py`'s tool-calling client, role `ORCHESTRATE` (ADR-031).

    The orchestrator never constructs an OpenAI client. Per-role routing, the token
    budget and record/replay live in that one module and none of them survive a second
    call site.
    """

    async def converse(
        self,
        role: Any,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        system: str | None = None,
        doc_id: str = "",
        on_text: Any = None,
    ) -> Any: ...


@runtime_checkable
class Embedder(Protocol):
    """Sentence embeddings at `settings.embedding_dimensions` (ADR-016). One model."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


__all__ = [
    "CommandGateway",
    "ConversationModel",
    "DocumentReader",
    "Embedder",
    "ExportGateway",
    "IngestGateway",
    "RetrievalIntrospector",
    "ReviewGateway",
    "SourceReader",
    "StyleGateway",
    "VersionGateway",
]
