"""The HTTP surface: the two-step edit flow, the approval gate, and HR-2 at startup."""

from __future__ import annotations

import asyncio
import json

import pytest
from b3_fakes import (
    BagOfWordsEmbedder,
    FakeFingerprintStore,
    InMemoryDocumentStore,
    ScriptedClaims,
    ScriptedPlanner,
    ScriptedRetrieval,
    ScriptedTextModel,
    ScriptedVerifier,
)
from b3_support import TEST_BAND, AlwaysRenders
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


class StubStyle:
    """B1's payload shape, reproduced exactly — including the absent `doc_id`.

    That absence is the whole point. A stub that helpfully returned one would agree with
    `StyleResponse` and prove nothing about the route, which is how a 500 on every
    successful style detection survived a passing suite.
    """

    def detect(self, doc_id: str) -> dict:
        return {
            "style_id": "apa",
            "score": 0.91,
            "similarity": 0.91,
            "ambiguous": False,
            "chosen_by_user": False,
            "shortlist": [{"style_id": "apa", "family": "author-date"}],
        }

    def select(self, doc_id: str, style_id: str) -> dict:
        return {
            "style_id": style_id,
            "score": None,
            "ambiguous": False,
            "chosen_by_user": True,
            "shortlist": [{"style_id": style_id, "family": "author-date"}],
        }


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
        style=StubStyle(),
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
        raise MissingAPIKeyError("OPENALEX_API_KEY is required and was not set")

    monkeypatch.setattr(main, "build_services", explode)

    with pytest.raises(MissingAPIKeyError, match="OPENALEX_API_KEY"):
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


def test_the_style_routes_answer_rather_than_500(base_document, sources):
    """The success path, which had no test at all.

    B1's payload has no `doc_id` and `StyleResponse` requires one, so
    `StyleResponse.model_validate(result)` raised a pydantic `ValidationError` and both
    routes returned 500 on every real detection. The route supplies it from the path.
    """
    client, _services, _documents = build_client(base_document, sources)
    doc_id = base_document.doc_id

    detected = client.get(f"/api/documents/{doc_id}/style")
    assert detected.status_code == 200, detected.text
    assert detected.json()["doc_id"] == doc_id
    assert detected.json()["style_id"] == "apa"

    chosen = client.put(f"/api/documents/{doc_id}/style", json={"style_id": "ieee"})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json() == {**chosen.json(), "doc_id": doc_id, "style_id": "ieee"}


def test_health_reports_what_is_bound(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    body = client.get("/api/health").json()
    assert body["bound"]["documents"] is True
    # `ingest` as the example of an unbound collaborator, not `style`: these tests now
    # bind a style service in order to exercise the style routes at all.
    assert "ingest" in body["unbound"]
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


def test_the_export_manifest_route_exists_and_describes_the_download(base_document, sources):
    """F1 has called `/export/manifest` since the export screen was written; the API never
    served it, so every visit to that screen ended in "could not load the export" while
    the openapi schema listed only `export.tex`."""
    client, _services, _documents = build_client(base_document, sources)
    doc_id = base_document.doc_id

    response = client.get(f"/api/documents/{doc_id}/export/manifest")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["doc_id"] == doc_id
    assert body["version"] == base_document.version
    # The name the manifest promises must be the name the download delivers.
    download = client.get(f"/api/documents/{doc_id}/export.tex")
    assert body["filename"] in download.headers["content-disposition"]
    # Two distinct source_ids across the seeded anchors, counted off the IR.
    assert body["bibliography_entries"] == 2
    assert {p["type"] for p in body["placeholder_blocks"]} == {"figure", "table", "equation"}


def test_a_document_with_no_style_reports_the_export_as_blocked(base_document, sources):
    """HR-3. Style detection returns `ambiguous` rather than guessing (CP-3), so a paper
    can sit at the export screen with no style — and the exporter then refuses. The
    manifest has to say so *before* the click, or the refusal reaches the user as a
    broken download instead of as the decision it is."""
    base_document.metadata.style_id = None
    client, _services, _documents = build_client(base_document, sources)

    body = client.get(f"/api/documents/{base_document.doc_id}/export/manifest").json()
    assert body["style_id"] is None
    assert body["exportable"] is False
    assert "citation style" in body["blocked_reason"]


def test_an_export_refusal_is_a_409_with_the_reason_not_a_bare_500(base_document, sources):
    """`ExportFailure` names a condition the exporter understands precisely. Uncaught, it
    left the route as a 500 reading "Internal Server Error" with the reason only in the
    container log — the generic 500 standing in for a known condition that HR-3 forbids."""
    from app.core.errors import ExportFailure

    class RefusingExporter:
        async def to_latex(self, document) -> str:  # noqa: ANN001
            raise ExportFailure("no citation style selected.")

    client, services, _documents = build_client(base_document, sources)
    services.exporter = RefusingExporter()

    response = client.get(f"/api/documents/{base_document.doc_id}/export.tex")
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"] == "export_refused"
    assert "no citation style selected." in body["detail"]


def test_choosing_a_style_persists_it_onto_the_stored_document(base_document, sources):
    """The choice has to land where the exporter reads.

    B1's `select()` writes `metadata.style_id` on the in-process parse record only. The
    exporter loads the head from the document store, where it stayed `None` — so a user
    who picked a style still got "no citation style selected" from the download, and the
    pick was lost outright on the next API restart.
    """
    base_document.metadata.style_id = None
    client, _services, documents = build_client(base_document, sources)
    doc_id = base_document.doc_id

    assert client.put(f"/api/documents/{doc_id}/style", json={"style_id": "ieee"}).status_code == 200

    # Read it back off the store the exporter uses, not off the style service's reply.
    head = asyncio.run(documents.get(doc_id))
    assert head.metadata.style_id == "ieee"
    assert head.metadata.style_ambiguous is False

    manifest = client.get(f"/api/documents/{doc_id}/export/manifest").json()
    assert manifest["style_id"] == "ieee"
    assert manifest["exportable"] is True
    assert manifest["blocked_reason"] is None


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
# Sources
# ===========================================================================


def test_a_source_record_is_readable_by_id(base_document, sources):
    """Every screen that renders a citation reads it here. Nothing served this route, so
    the parse inspector, the review feed and the edit console each fetched, got a 404 and
    rendered a citation-shaped hole."""
    client, _services, _documents = build_client(base_document, sources)

    response = client.get("/api/sources/s2:aaa")
    assert response.status_code == 200
    assert response.json()["source_id"] == "s2:aaa"

    # Warmed before it was read — an unwarmed id raises in B2's store rather than
    # reporting absence, so skipping the warm turns "we never looked" into a 500.
    assert "s2:aaa" in sources.warmed


def test_an_unknown_source_is_a_404_not_an_empty_record(base_document, sources):
    client, _services, _documents = build_client(base_document, sources)
    response = client.get("/api/sources/s2:never-stored")
    assert response.status_code == 404
    assert "s2:never-stored" in response.json()["detail"]


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


def test_the_advertised_stream_url_is_one_this_app_actually_serves(base_document, sources):
    """The 202 names its own stream URL — so *follow it* rather than asserting the string.

    Hard-coding the expected path above proves the response body and proves nothing about
    the router. The frontend composed `/api/reviews/{job_id}/stream` instead of following
    this link: no router has ever served that shape, the identifier was a job id where the
    runner keys by document, and every review 404'd — reported to the user as "the review
    could not run". A test that asks the app to resolve its own advertisement is the one
    that would have caught it.
    """
    events = [("progress", {"verified": 0, "total": 1}), ("complete", {"verified": 1, "total": 1})]
    client, _services, _documents = build_client(base_document, sources, review=StubReview(events))

    accepted = client.post(f"/api/documents/{base_document.doc_id}/review", json={}).json()
    for link in (accepted["stream"], accepted["poll"]):
        with client.stream("GET", link) as response:
            assert response.status_code == 200, f"the API advertised {link}, which it does not serve"
            body = "".join(response.iter_text())
        assert body, f"{link} answered with nothing"

    with client.stream("GET", accepted["stream"]) as response:
        assert "event: complete" in "".join(response.iter_text())


# ===========================================================================
# ADR-015 — one client, one place a model is named
# ===========================================================================


def test_no_model_id_or_sdk_import_appears_anywhere_in_b3():
    """`settings.model_for(role)` is the only sanctioned way to name a model, and
    `app/core/llm.py` is the only path to the API.

    Checked mechanically because the failure is invisible: a second client still works,
    it just silently bypasses per-role routing, the token budget and record/replay — and
    then CI, which is supposed to make zero live calls, starts making them.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[3] / "app"
    model_id = re.compile(r"\bgpt-[0-9]|\bclaude-[a-z0-9]|\bvoyage-[0-9]|text-embedding-")
    sdk_import = re.compile(r"^\s*(?:from|import)\s+(anthropic|openai|voyageai)\b", re.MULTILINE)

    offenders: list[str] = []
    for package in ("agent", "api"):
        for path in sorted((root / package).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if model_id.search(text):
                offenders.append(f"{path.relative_to(root)}: names a model id")
            if sdk_import.search(text):
                offenders.append(f"{path.relative_to(root)}: imports a model SDK")

    assert not offenders, "\n".join(offenders)
