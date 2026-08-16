"""The HTTP surface: the two-step edit flow, the approval gate, and HR-2 at startup."""

from __future__ import annotations

import json

import pytest
from conftest import TEST_BAND, AlwaysRenders
from fakes import (
    BagOfWordsEmbedder,
    FakeFingerprintStore,
    InMemoryDocumentStore,
    ScriptedClaims,
    ScriptedPlanner,
    ScriptedRetrieval,
    ScriptedTextModel,
    ScriptedVerifier,
)
from fastapi.testclient import TestClient

from app.api.deps import Services
from app.api.main import create_app
from app.core.contracts import MissingAPIKeyError, VerificationLabel

SHORTEN_PLAN = {"operations": [{"op": "Shorten", "target_ids": ["blk-1"], "params": {"ratio": 0.6}}]}
FABRICATING_PLAN = {
    "operations": [
        {
            "op": "ReplaceCitation",
            "target_ids": ["span-1"],
            "params": {"anchor_id": "anc-1", "new_source_id": "s2:fabricated"},
        }
    ]
}
CLEAN_REWRITE = "Transformers dominate sequence modelling. Attention scales quadratically with length."
DESTRUCTIVE_REWRITE = "Entirely unrelated content about protein folding in yeast."


class StubExporter:
    async def to_latex(self, document) -> str:  # noqa: ANN001
        return f"% {document.doc_id} v{document.version}\n\\section{{Introduction}}\n"


class StubReview:
    def __init__(self, events=None, fail=False) -> None:
        self._events = events or []
        self._fail = fail

    async def start(self, doc_id: str, section_ids=None) -> str:
        return "job-review-1"

    async def status(self, doc_id: str) -> dict:
        return {"doc_id": doc_id, "state": "running", "verified": 1, "total": 4}

    async def stream(self, doc_id: str, section_ids=None):
        for event in self._events:
            yield event
        if self._fail:
            raise RuntimeError("Semantic Scholar returned 429")


def build_client(base_document, sources, *, planner_responses=None, text_output=CLEAN_REWRITE,
                 review=None) -> tuple[TestClient, Services, InMemoryDocumentStore]:
    documents = InMemoryDocumentStore()
    documents.seed(base_document)
    services = Services(
        documents=documents,
        sources=sources,
        render_probe=AlwaysRenders(),
        exporter=StubExporter(),
        retrieval=ScriptedRetrieval(default=["openalex:W123"]),
        verifier=ScriptedVerifier(default=VerificationLabel.SUPPORTS),
        claims=ScriptedClaims([]),
        review=review,
        embedder=BagOfWordsEmbedder(),
        text_model=ScriptedTextModel(text_output),
        structured_model=ScriptedPlanner(planner_responses or [SHORTEN_PLAN]),
        fingerprints=FakeFingerprintStore(),
        band=TEST_BAND,
        settings=None,
    )
    return TestClient(create_app(services)), services, documents


# ===========================================================================
# HR-2 — the application must not come up misconfigured
# ===========================================================================


def test_a_missing_api_key_aborts_startup_rather_than_degrading(monkeypatch):
    """ADR-010: no anonymous mode, no banner, no default route. The factory must let the
    configuration error out of the building."""
    import app.api.main as main

    def explode() -> Services:
        raise MissingAPIKeyError("SEMANTIC_SCHOLAR_API_KEY is required and was not set")

    monkeypatch.setattr(main, "build_services", explode)

    with pytest.raises(MissingAPIKeyError, match="SEMANTIC_SCHOLAR_API_KEY"):
        main.create_app()


def test_a_configuration_error_at_request_time_is_named_not_a_generic_500(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)

    @client.app.get("/api/boom")
    async def boom():  # noqa: ANN202
        raise MissingAPIKeyError("OPENALEX_API_KEY is required and was not set")

    response = client.get("/api/boom")
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "configuration_error"
    assert "OPENALEX_API_KEY" in body["detail"]
    assert "no anonymous mode" in body["hint"]


def test_an_unbound_collaborator_is_a_503_that_names_it(base_document, sources):
    """No stub retrieval returning [], no null renderer. A missing dependency says so."""
    client, services, _documents = build_client(base_document, sources)
    services.style = None

    response = client.get(f"/api/documents/{base_document.doc_id}/style")
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "dependency_unavailable"
    assert body["component"] == "style"
    assert "memory.md" in body["detail"]


def test_health_reports_what_is_bound(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    body = client.get("/api/health").json()
    assert body["bound"]["documents"] is True
    assert "style" in body["unbound"]
    assert "never a silent fallback" in body["note"]


# ===========================================================================
# Documents
# ===========================================================================


def test_get_document_returns_the_ir(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    body = client.get(f"/api/documents/{base_document.doc_id}").json()
    assert body["doc_id"] == "doc-1"
    assert [s["title"] for s in body["sections"]] == ["Introduction", "Method"]


def test_an_unknown_document_is_a_404_with_a_reason(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    response = client.get("/api/documents/doc-nope")
    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]


def test_export_downloads_the_tex(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    response = client.get(f"/api/documents/{base_document.doc_id}/export.tex")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-tex")
    assert "attachment" in response.headers["content-disposition"]
    assert "\\section{Introduction}" in response.text


# ===========================================================================
# The edit flow
# ===========================================================================


def test_a_command_proposes_without_writing_anything(base_document, sources):
    client, _services, documents = build_client(base_document, sources)
    response = client.post(
        f"/api/documents/{base_document.doc_id}/commands", json={"command": "shorten the intro"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "awaiting_approval"
    assert len(body["changes"]) == 1
    assert body["changes"][0]["diff"]["citations"]["preserved"] is True

    import asyncio

    assert asyncio.run(documents.list_versions("doc-1")) == [1], "no version was written"


def test_the_proposal_can_be_fetched_again_by_id(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    change_set_id = client.post(
        f"/api/documents/{base_document.doc_id}/commands", json={"command": "shorten the intro"}
    ).json()["change_set_id"]

    body = client.get(f"/api/change-sets/{change_set_id}").json()
    assert body["change_set_id"] == change_set_id


def test_an_expired_change_set_is_a_404_not_an_empty_answer(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    response = client.get("/api/change-sets/cs-gone")
    assert response.status_code == 404
    assert "re-issue the command" in response.json()["detail"]


def test_approval_commits_a_new_version(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    proposal = client.post(
        f"/api/documents/{base_document.doc_id}/commands", json={"command": "shorten the intro"}
    ).json()

    response = client.post(
        f"/api/change-sets/{proposal['change_set_id']}/approve",
        json={
            "base_version": proposal["base_version"],
            "approved_change_ids": [c["change"]["change_id"] for c in proposal["changes"]],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["committed"] is True
    assert body["new_version"] == 2
    assert body["diff"]["citations"]["preserved"] is True
    assert "revertible" in body["message"]


def test_approving_nothing_writes_nothing(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    proposal = client.post(
        f"/api/documents/{base_document.doc_id}/commands", json={"command": "shorten the intro"}
    ).json()

    body = client.post(
        f"/api/change-sets/{proposal['change_set_id']}/approve", json={"base_version": proposal["base_version"], "approved_change_ids": []}
    ).json()
    assert body["committed"] is False


def test_an_undecided_orphan_blocks_approval_with_a_409(base_document, sources):
    client, _services, _documents = build_client(
        base_document, sources, text_output=DESTRUCTIVE_REWRITE
    )
    proposal = client.post(
        f"/api/documents/{base_document.doc_id}/commands", json={"command": "shorten the intro"}
    ).json()
    assert proposal["changes"][0]["orphans"]

    response = client.post(
        f"/api/change-sets/{proposal['change_set_id']}/approve",
        json={
            "base_version": proposal["base_version"],
            "approved_change_ids": [c["change"]["change_id"] for c in proposal["changes"]],
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "approval_invalid"
    assert "waiting on your decision" in response.json()["detail"]


def test_the_orphan_decision_payload_carries_what_the_ui_needs(base_document, sources):
    client, _services, _documents = build_client(
        base_document, sources, text_output=DESTRUCTIVE_REWRITE
    )
    proposal = client.post(
        f"/api/documents/{base_document.doc_id}/commands", json={"command": "shorten the intro"}
    ).json()

    orphan = proposal["changes"][0]["orphans"][0]
    assert orphan["actions"] == ["keep", "move", "remove"]
    assert orphan["source_ids"]
    assert orphan["marker"]
    assert orphan["best_span_text"], "the UI needs somewhere to show 'keep it here'"


def test_a_rejected_plan_surfaces_its_reason_to_the_caller(base_document, sources):
    client, _services, _documents = build_client(
        base_document, sources, planner_responses=[FABRICATING_PLAN] * 3
    )
    body = client.post(
        f"/api/documents/{base_document.doc_id}/commands", json={"command": "swap that citation"}
    ).json()

    assert body["status"] == "failed"
    assert body["attempts"] == 3
    assert any("not in the source store" in r for r in body["rejected"][0]["reasons"])


def test_a_failed_change_set_cannot_be_approved(base_document, sources):
    client, _services, _documents = build_client(
        base_document, sources, planner_responses=[FABRICATING_PLAN] * 3
    )
    proposal = client.post(
        f"/api/documents/{base_document.doc_id}/commands", json={"command": "swap that citation"}
    ).json()

    response = client.post(
        f"/api/change-sets/{proposal['change_set_id']}/approve", json={"base_version": proposal["base_version"], "approved_change_ids": []}
    )
    assert response.status_code == 409
    assert "nothing to approve" in response.json()["detail"]


def test_versions_and_revert(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    proposal = client.post(
        f"/api/documents/{base_document.doc_id}/commands", json={"command": "shorten the intro"}
    ).json()
    client.post(
        f"/api/change-sets/{proposal['change_set_id']}/approve",
        json={
            "base_version": proposal["base_version"],
            "approved_change_ids": [c["change"]["change_id"] for c in proposal["changes"]],
        },
    )

    versions = client.get(f"/api/documents/{base_document.doc_id}/versions").json()
    assert versions == {"doc_id": "doc-1", "versions": [1, 2], "current": 2}

    reverted = client.post(f"/api/documents/{base_document.doc_id}/revert", json={"to_version": 1}).json()
    assert reverted["committed"] is True
    assert reverted["new_version"] == 3


def test_the_freeform_metric_is_exposed(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    body = client.get("/api/agent/metrics").json()
    assert body["tripwire"] == 0.20
    assert "freeform_command_rate" in body


# ===========================================================================
# SSE
# ===========================================================================


def test_the_review_stream_emits_findings_and_completes(base_document, sources):
    events = [
        ("progress", {"verified": 0, "total": 2}),
        ("finding", {"finding_id": "f1", "kind": "missing_work", "severity": "high"}),
        ("progress", {"verified": 2, "total": 2}),
        ("complete", {"verified": 2, "total": 2, "findings": 1}),
    ]
    client, _services, _documents = build_client(base_document, sources, review=StubReview(events))

    with client.stream("GET", f"/api/documents/{base_document.doc_id}/review/stream") as response:
        assert response.status_code == 200
        assert response.headers["x-accel-buffering"] == "no"
        body = "".join(response.iter_text())

    assert "event: finding" in body
    assert "event: complete" in body
    assert json.dumps({"verified": 2, "total": 2, "findings": 1}) in body


def test_a_failing_review_stream_reports_the_error_rather_than_ending_quietly(base_document, sources):
    """HR-3: a stream that stops must say why, or a partial review reads as a clean one."""
    client, _services, _documents = build_client(
        base_document,
        sources,
        review=StubReview([("finding", {"finding_id": "f1"})], fail=True),
    )

    with client.stream("GET", f"/api/documents/{base_document.doc_id}/review/stream") as response:
        body = "".join(response.iter_text())

    assert "event: error" in body
    assert "429" in body
    assert "partial" in body
    assert "event: complete" not in body


def test_starting_a_review_returns_a_job_id_and_the_stream_url(base_document, sources):
    client, _services, _documents = build_client(base_document, sources, review=StubReview())
    response = client.post(f"/api/documents/{base_document.doc_id}/review", json={})
    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == "job-review-1"
    assert body["stream"] == "/api/documents/doc-1/review/stream"
