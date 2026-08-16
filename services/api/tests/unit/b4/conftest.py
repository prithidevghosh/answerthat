"""Fixtures only. The importable helpers live in `b4_fakes.py` — see the note there."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from b4_fakes import (  # noqa: E402
    FakeCommands,
    FakeDocuments,
    FakeEmbedder,
    FakeExporter,
    FakeIndex,
    FakeIngest,
    FakeRetrieval,
    FakeReview,
    FakeSettings,
    FakeSourceReader,
    FakeStyle,
    FakeVersions,
    make_document,
    make_source,
)

from app.orchestrator.session import InMemoryConversationStore  # noqa: E402
from app.orchestrator.tools import ToolContext, build_registry  # noqa: E402


@pytest.fixture
def documents() -> FakeDocuments:
    store = FakeDocuments()
    store.add(make_document())
    return store


@pytest.fixture
def sources() -> FakeSourceReader:
    return FakeSourceReader({"s2:aaa": make_source("s2:aaa")})


@pytest.fixture
def ingest() -> FakeIngest:
    return FakeIngest(
        status={
            "state": "complete",
            "stage": "complete",
            "progress": 1.0,
            "version": 1,
            "elapsed_s": 42.0,
            "error": None,
        },
        report={
            "counts": {
                "total_detected": 47,
                "resolved": 39,
                "parsed_unresolved": 5,
                "low_confidence": 2,
                "quarantined": 1,
                "orphan_marker": 3,
            },
            "references": [{"ref_id": "ref-1"}],
            "orphan_markers": [{"anchor_id": "anc-9"}],
        },
    )


@pytest.fixture
def versions() -> FakeVersions:
    return FakeVersions()


@pytest.fixture
def commands() -> FakeCommands:
    return FakeCommands()


@pytest.fixture
def review() -> FakeReview:
    return FakeReview()


@pytest.fixture
def exporter() -> FakeExporter:
    return FakeExporter()


@pytest.fixture
def index() -> FakeIndex:
    return FakeIndex()


@pytest.fixture
def settings() -> FakeSettings:
    return FakeSettings()


@pytest.fixture
def context(
    ingest, documents, sources, review, commands, versions, exporter, index, settings
) -> ToolContext:
    return ToolContext(
        ingest=ingest,
        documents=documents,
        sources=sources,
        style=FakeStyle(),
        review=review,
        retrieval=FakeRetrieval(
            ["s2_snippet", "s2_recommendations", "openalex_search", "openalex_graph"], []
        ),
        commands=commands,
        versions=versions,
        exporter=exporter,
        index=index,
        settings=settings,
    )


@pytest.fixture
def registry(context: ToolContext):
    return build_registry(context, "doc-1")


@pytest.fixture
def conversations():
    return InMemoryConversationStore()


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()
