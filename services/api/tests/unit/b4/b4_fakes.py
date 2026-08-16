"""Fakes for the orchestrator suite, in a uniquely named module.

Named `b4_fakes` rather than living in `conftest.py` for the reason `b3_support.py`
records: `from conftest import …` resolves through `sys.modules["conftest"]`, and every
suite directory has one, so which module the name refers to depends on collection order.

Two of these fakes deliberately keep a sharp edge rather than smoothing it:

* `FakeSourceReader.get` returns None for an id nobody put there, which is what makes the
  HR-1 test meaningful — a fake that invented a record on demand would let a fabricated
  `source_id` sail through here and fail only in production.
* `ScriptedModel` returns exactly the turns it was given and raises when it runs out. A
  fake that repeated its last turn would make the iteration-cap test pass for the wrong
  reason.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.core.contracts import (
    Block,
    CitationAnchor,
    Document,
    DocumentMeta,
    Section,
    SourceRecord,
    Span,
)
from app.core.llm import AssistantTurn, ToolCall

# --------------------------------------------------------------------------- settings


class FakeSettings:
    """Only the fields the orchestrator reads. ADR-024's values where they overlap."""

    orchestrator_max_iterations = 6
    orchestrator_context_budget_tokens = 100_000
    orchestrator_chars_per_token = 4
    orchestrator_watch_interval_s = 0.01
    orchestrator_search_k = 8
    orchestrator_search_k_max = 25
    orchestrator_page_size = 20
    orchestrator_page_size_max = 100
    orchestrator_index_text_chars = 2_000
    orchestrator_index_batch = 64
    rerank_keep = 10
    verify_keep = 3
    citability_min = 0.3
    embedding_model = "text-embedding-3-small"
    embedding_dimensions = 4


# --------------------------------------------------------------------------- documents


def make_document(doc_id: str = "doc-1", version: int = 1, *, title: str = "A Paper") -> Document:
    return Document(
        doc_id=doc_id,
        version=version,
        metadata=DocumentMeta(title=title, style_id="ieee"),
        sections=[
            Section(
                id="sec-1",
                level=1,
                title="Introduction",
                order=0,
                blocks=[
                    Block(
                        id="blk-1",
                        type="paragraph",
                        order=0,
                        spans=[
                            Span(
                                id="span-1",
                                text="Transformers dominate sequence modelling.",
                                citation_anchors=[
                                    CitationAnchor(
                                        anchor_id="anc-1",
                                        source_ids=["s2:aaa"],
                                        offset_in_span=40,
                                        original_marker_text="[1]",
                                    )
                                ],
                            ),
                            Span(id="span-2", text="Attention scales quadratically."),
                        ],
                    )
                ],
            )
        ],
    )


# --------------------------------------------------------------------------- gateways


class FakeIngest:
    def __init__(self, status: dict | None = None, report: dict | None = None) -> None:
        self._status = status
        self._report = report
        self.draft: Document | None = None
        self.failure: Exception | None = None

    def status(self, doc_id: str) -> dict | None:  # noqa: ARG002
        return self._status

    async def parse_report(self, doc_id: str, version: int | None = None) -> dict:  # noqa: ARG002
        if self.failure is not None:
            raise self.failure
        return self._report or {}

    def draft_document(self, doc_id: str) -> Document | None:  # noqa: ARG002
        return self.draft


class FakeDocuments:
    def __init__(self, documents: dict[tuple[str, int | None], Document] | None = None) -> None:
        self.by_doc: dict[str, list[Document]] = {}
        for (doc_id, _v), document in (documents or {}).items():
            self.by_doc.setdefault(doc_id, []).append(document)

    def add(self, document: Document) -> None:
        self.by_doc.setdefault(document.doc_id, []).append(document)

    async def get(self, doc_id: str, version: int | None = None) -> Document | None:
        versions = self.by_doc.get(doc_id) or []
        if not versions:
            return None
        if version is None:
            return max(versions, key=lambda d: d.version)
        return next((d for d in versions if d.version == version), None)

    async def list_versions(self, doc_id: str) -> list[int]:
        return sorted(d.version for d in self.by_doc.get(doc_id, []))


class FakeSourceReader:
    """Read-only, and there is deliberately no `put`. HR-1."""

    def __init__(self, records: dict[str, SourceRecord] | None = None) -> None:
        self._records = records or {}
        self.warmed: list[str] = []

    async def warm(self, source_ids: list[str]) -> None:
        self.warmed.extend(source_ids)

    def get(self, source_id: str) -> SourceRecord | None:
        return self._records.get(source_id)

    def has(self, source_id: str) -> bool:
        return source_id in self._records


def make_source(source_id: str = "s2:aaa", *, abstract: str = "An abstract.") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        csl={"id": source_id, "type": "article-journal", "title": f"Paper {source_id}"},
        provenance={
            "provider": "semantic_scholar",
            "endpoint": "/paper/search/match",
            "retrieved_at": "2026-01-01T00:00:00Z",
            "external_url": f"https://example.org/{source_id}",
        },
        abstract=abstract,
        abstract_source="s2",
    )


class FakeStyle:
    def __init__(self) -> None:
        self.selected: list[tuple[str, str]] = []

    def detect(self, doc_id: str) -> dict:  # noqa: ARG002
        return {"style_id": "ieee", "score": 0.91, "ambiguous": False, "shortlist": []}

    def select(self, doc_id: str, style_id: str) -> dict:
        self.selected.append((doc_id, style_id))
        return {"style_id": style_id, "score": None, "ambiguous": False, "chosen_by_user": True}


class FakeReview:
    def __init__(self, *, status: dict | None = None, findings: list[dict] | None = None) -> None:
        self._status = status or {"status": "not_started", "events_emitted": 0}
        self._findings = findings or []
        self.started: list[tuple[str, Any, bool]] = []
        self.events: list[tuple[str, dict]] = []

    def start(self, doc_id: str, section_ids: list[str] | None = None, *, force: bool = False) -> str:
        self.started.append((doc_id, section_ids, force))
        return "rev_fake"

    def status(self, doc_id: str) -> dict:  # noqa: ARG002
        return self._status

    def findings(self, doc_id: str) -> list[dict]:  # noqa: ARG002
        return self._findings

    async def stream(self, doc_id: str):  # noqa: ARG002
        for event in self.events:
            yield event


class FakeRetrieval:
    def __init__(self, will_run: list[str], will_not_run: list[str]) -> None:
        self._will_run = will_run
        self._will_not_run = will_not_run

    async def strategies_for(self, doc_id: str) -> tuple[list[str], list[str]]:  # noqa: ARG002
        return list(self._will_run), list(self._will_not_run)


class FakeCommands:
    """Stands in for the command loop and the change-set store."""

    def __init__(self, change_set: dict | None = None) -> None:
        self.change_set = change_set or {}
        self.proposed: list[str] = []

    async def propose(self, document: Document, instruction: str) -> dict:  # noqa: ARG002
        self.proposed.append(instruction)
        return dict(self.change_set)

    def get_change_set(self, change_set_id: str) -> dict:
        from app.agent.store import ChangeSetNotFound

        if self.change_set.get("change_set_id") != change_set_id:
            raise ChangeSetNotFound(f"change set {change_set_id!r} is not held any more.")
        return dict(self.change_set)


@dataclass
class FakeVersions:
    """Records every write. The confirmation-gate tests assert this list is empty."""

    commits: list[dict] = field(default_factory=list)
    reverts: list[tuple[str, int]] = field(default_factory=list)
    styles: list[Document] = field(default_factory=list)
    raises: Exception | None = None
    undecided_orphans: list[str] = field(default_factory=list)

    async def commit(
        self,
        change_set_id: str,
        *,
        base_version: int,
        approved_change_ids: list[str],
        rejected_change_ids: list[str],
        orphan_decisions: list[dict],
    ) -> dict:
        if self.raises is not None:
            raise self.raises
        # Mirrors `VersionService._check_orphans_resolved`: an undecided anchor blocks the
        # commit and names itself. Reproduced rather than stubbed away, because "a plain
        # yes cannot settle an orphan" is the property under test.
        decided = {decision["anchor_id"] for decision in orphan_decisions}
        outstanding = [a for a in self.undecided_orphans if a not in decided]
        if outstanding:
            from app.agent.versioning import ApprovalError

            raise ApprovalError(
                f"{len(outstanding)} citation(s) are still waiting on your decision "
                f"({sorted(outstanding)}). Choose keep, move, or remove for each before "
                "committing."
            )
        self.commits.append(
            {
                "change_set_id": change_set_id,
                "base_version": base_version,
                "approved_change_ids": approved_change_ids,
                "orphan_decisions": orphan_decisions,
            }
        )
        return {
            "committed": True,
            "doc_id": "doc-1",
            "base_version": base_version,
            "new_version": base_version + 1,
            "applied_change_ids": approved_change_ids,
            "message": f"Committed version {base_version + 1}.",
        }

    async def revert(self, doc_id: str, to_version: int) -> dict:
        self.reverts.append((doc_id, to_version))
        return {"committed": True, "doc_id": doc_id, "message": f"Reverted to {to_version}."}

    async def set_style(self, document: Document) -> int:
        self.styles.append(document)
        return document.version + 1


class FakeExporter:
    def __init__(self, manifest: dict | None = None) -> None:
        self._manifest = manifest or {
            "doc_id": "doc-1",
            "version": 1,
            "filename": "doc-1-v1.tex",
            "placeholder_blocks": [
                {"type": "figure", "count": 2},
                {"type": "table", "count": 1},
                {"type": "equation", "count": 0},
            ],
            "bibliography_entries": 12,
            "style_id": "ieee",
            "style_uncertain": False,
            "exportable": True,
            "blocked_reason": None,
        }
        self.rendered: list[tuple[str, int | None]] = []

    async def manifest(self, doc_id: str, version: int | None = None) -> dict:  # noqa: ARG002
        return dict(self._manifest)

    async def to_latex(self, doc_id: str, version: int | None = None) -> dict:
        self.rendered.append((doc_id, version))
        return {
            "filename": "doc-1-v1.tex",
            "byte_size": 2048,
            "download_url": f"/api/documents/{doc_id}/export.tex?version=1",
            "version": 1,
            "style_id": "ieee",
            "style_uncertain": False,
        }


class FakeEmbedder:
    """Deterministic 4-dimensional embeddings from a bag of characters.

    Crude on purpose: the index tests are about *whether the right rows are searched and
    what the status says*, not about retrieval quality, and a fake that pretended to
    semantic accuracy would invite tests that assert on it.
    """

    def __init__(self, dimensions: int = 4) -> None:
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for index, char in enumerate(text.lower()):
                vector[index % self.dimensions] += (ord(char) % 7) / 10.0
            vectors.append(vector or [0.0] * self.dimensions)
        return vectors


class FakeIndex:
    def __init__(self, hits: list | None = None, status: dict | None = None) -> None:
        self._hits = hits or []
        self._status = status or {"state": "ready", "kinds_indexed": [], "kinds_missing": [], "rows": 0}
        self.scheduled: list[tuple[str, list]] = []

    async def search(self, doc_id: str, query: str, *, k: int, kinds=None):  # noqa: ARG002
        return self._hits[:k], dict(self._status)

    def schedule(self, doc_id: str, texts: list) -> None:
        self.scheduled.append((doc_id, texts))


# --------------------------------------------------------------------------- model


class ScriptedModel:
    """Returns pre-written `AssistantTurn`s, one per `converse()` call.

    Raises when the script runs out rather than repeating: a model that kept answering
    would make an iteration-cap test pass whether or not the cap works.
    """

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[dict] = []
        self.raises: Exception | None = None

    async def converse(
        self,
        role,  # noqa: ANN001
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        system: str | None = None,
        doc_id: str = "",
        on_text=None,  # noqa: ANN001
    ):
        self.calls.append(
            {"role": role, "messages": messages, "tools": tools, "system": system, "doc_id": doc_id}
        )
        if self.raises is not None:
            raise self.raises
        if not self.turns:
            raise AssertionError(
                f"ScriptedModel ran out of turns after {len(self.calls)} call(s); the loop "
                "asked for one more than the test scripted."
            )
        turn = self.turns.pop(0)
        if on_text is not None and turn.text:
            await on_text(turn.text)
        return turn


def say(text: str) -> AssistantTurn:
    return AssistantTurn(text=text, tool_calls=[], finish_reason="stop", tokens=10)


def call(name: str, arguments: dict, *, call_id: str = "", text: str = "") -> AssistantTurn:
    return AssistantTurn(
        text=text,
        tool_calls=[ToolCall(call_id=call_id or f"call_{name}", name=name, arguments=arguments)],
        finish_reason="tool_calls",
        tokens=10,
    )


def multi(*calls: ToolCall, text: str = "") -> AssistantTurn:
    return AssistantTurn(
        text=text, tool_calls=list(calls), finish_reason="tool_calls", tokens=10
    )


async def settle() -> None:
    """Let background turn tasks finish.

    The orchestrator runs a turn with `asyncio.create_task`, so a test that asserts
    immediately after `send_user_message` asserts on nothing. Yielding repeatedly rather
    than sleeping a fixed time keeps the suite fast and free of a timing constant nobody
    can justify.
    """
    for _ in range(60):
        await asyncio.sleep(0)
