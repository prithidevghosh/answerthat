"""Two ways this system fails invisibly, and the code that makes both visible.

ADR-022 — a background worker that dies leaves the UI streaming nothing, which a
researcher reads as "no findings". ADR-021 — a second command approved against a stale
head silently discards the first one, and the user sees a successful approval over a
document missing their earlier edit.

Neither shows up as an error anywhere. That is what these tests are for.
"""

from __future__ import annotations

from datetime import timedelta

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

from app.agent.executor import OperationExecutor
from app.agent.kernel import InvariantKernel
from app.agent.loop import CommandLoop
from app.agent.planner import Planner
from app.agent.versioning import ApprovalRequest, VersionConflict, VersionService
from app.api.deps import Services
from app.api.jobstore import InMemoryJobStore, JobView
from app.api.main import create_app
from app.core.contracts import JobStatus, VerificationLabel
from app.core.db import utcnow

SHORTEN_PLAN = {"operations": [{"op": "Shorten", "target_ids": ["blk-1"], "params": {"ratio": 0.6}}]}
CLEAN_REWRITE = "Transformers dominate sequence modelling. Attention scales quadratically with length."


# ===========================================================================
# ADR-022 — a job that stops reporting is a failure, not a silence
# ===========================================================================


@pytest.mark.asyncio
async def test_a_job_moves_through_its_states_and_records_why_it_failed():
    jobs = InMemoryJobStore()
    await jobs.create(job_id="job-1", kind="review", doc_id="doc-1")
    assert (await jobs.get("job-1")).status is JobStatus.QUEUED

    await jobs.start("job-1")
    await jobs.progress("job-1", 3, 40)
    running = await jobs.get("job-1")
    assert running.status is JobStatus.RUNNING
    assert (running.progress_current, running.progress_total) == (3, 40)

    await jobs.fail("job-1", "Semantic Scholar returned 429 after 3 retries")
    failed = await jobs.get("job-1")
    assert failed.status is JobStatus.FAILED
    assert "429" in failed.error


@pytest.mark.asyncio
async def test_a_worker_that_stops_reporting_is_reported_failed_not_running():
    """The SIGKILL case. Nobody is left to write the failure, so it is inferred — and the
    message says only what we actually know: the worker went quiet."""
    jobs = InMemoryJobStore(stale_after_seconds=60)
    await jobs.create(job_id="job-2", kind="review", doc_id="doc-1")
    await jobs.start("job-2")

    stale = (utcnow() - timedelta(minutes=30)).isoformat()
    jobs._jobs["job-2"] = jobs._jobs["job-2"].model_copy(update={"updated_at": stale})

    view = await jobs.get("job-2")
    assert view.status is JobStatus.FAILED
    assert "stopped reporting" in view.error
    assert "not 'nothing found'" in view.error


@pytest.mark.asyncio
async def test_a_slow_job_is_not_called_dead():
    jobs = InMemoryJobStore(stale_after_seconds=60 * 35)
    await jobs.create(job_id="job-3", kind="ingest", doc_id="doc-1")
    await jobs.start("job-3")

    recent = (utcnow() - timedelta(minutes=4)).isoformat()
    jobs._jobs["job-3"] = jobs._jobs["job-3"].model_copy(update={"updated_at": recent})

    assert (await jobs.get("job-3")).status is JobStatus.RUNNING


@pytest.mark.asyncio
async def test_a_status_update_for_an_unknown_job_does_not_invent_one():
    jobs = InMemoryJobStore()
    assert await jobs.succeed("never-started") is None
    assert await jobs.get("never-started") is None


@pytest.mark.asyncio
async def test_a_failed_row_is_not_argued_back_to_life_by_a_frozen_owner():
    """A killed worker leaves the owning pipeline's in-process state frozen at `running`.
    That must not overwrite the row that already says the worker is gone."""
    from app.api.routes.jobs import reconcile

    class FrozenOwner:
        def status(self, doc_id: str) -> dict:
            return {"doc_id": doc_id, "state": "running", "verified": 12, "total": 40}

    jobs = InMemoryJobStore(stale_after_seconds=60)
    await jobs.create(job_id="job-4", kind="review", doc_id="doc-1")
    await jobs.start("job-4")
    jobs._jobs["job-4"] = jobs._jobs["job-4"].model_copy(
        update={"updated_at": (utcnow() - timedelta(hours=2)).isoformat()}
    )

    dead = await jobs.get("job-4")
    services = Services(jobs=jobs, review=FrozenOwner())
    assert (await reconcile(services, dead)).status is JobStatus.FAILED


@pytest.mark.asyncio
async def test_a_live_owner_supplies_the_progress_the_row_does_not_have():
    from app.api.routes.jobs import reconcile

    class LiveOwner:
        def status(self, doc_id: str) -> dict:
            return {"doc_id": doc_id, "state": "running", "verified": 7, "total": 40}

    jobs = InMemoryJobStore()
    view = await jobs.create(job_id="job-5", kind="review", doc_id="doc-1")
    merged = await reconcile(Services(jobs=jobs, review=LiveOwner()), view)

    assert merged.status is JobStatus.RUNNING
    assert (merged.progress_current, merged.progress_total) == (7, 40)


def test_the_job_view_narrows_to_the_appendix_a_contract():
    view = JobView(
        job_id="job-6",
        kind="ingest",
        doc_id="doc-1",
        status=JobStatus.FAILED,
        error="GROBID never became healthy",
        created_at="2026-08-16T00:00:00+00:00",
        updated_at="2026-08-16T00:01:00+00:00",
    )
    contract = view.as_contract()
    assert contract.kind == "ingest"
    assert contract.status is JobStatus.FAILED
    assert contract.error == "GROBID never became healthy"


# ===========================================================================
# ADR-022 — the uploaded PDF is on disk before the ingest starts
# ===========================================================================


class StubIngest:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def enqueue(self, doc_id: str, filename: str, payload: bytes) -> str:
        self.calls.append(doc_id)
        return f"job-{doc_id}"


def upload_client(tmp_path, jobs, *, upload_dir=None) -> TestClient:
    services = Services(
        ingest=StubIngest(),
        jobs=jobs,
        upload_dir=upload_dir if upload_dir is not None else tmp_path,
    )
    return TestClient(create_app(services))


@pytest.mark.asyncio
async def test_the_pdf_is_written_to_the_volume_and_its_path_recorded(tmp_path):
    """A crashed ingest must be retryable. Bytes that live only in a dead worker's memory
    mean asking the researcher to upload their paper again for our fault (ADR-022)."""
    jobs = InMemoryJobStore()
    client = upload_client(tmp_path, jobs)

    response = client.post(
        "/api/documents", files={"file": ("paper.pdf", b"%PDF-1.7 fake", "application/pdf")}
    )
    assert response.status_code == 202
    body = response.json()

    written = tmp_path / f"{body['doc_id']}.pdf"
    assert written.read_bytes() == b"%PDF-1.7 fake"

    view = await jobs.get(body["job_id"])
    assert view.upload_path == str(written)
    assert view.kind == "ingest"


def test_an_unwritable_upload_directory_fails_the_upload_rather_than_the_parse(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("this is a file, so mkdir underneath it cannot succeed")

    client = upload_client(tmp_path, InMemoryJobStore(), upload_dir=blocked / "uploads")
    response = client.post(
        "/api/documents", files={"file": ("paper.pdf", b"%PDF-1.7", "application/pdf")}
    )
    assert response.status_code == 507
    assert "cannot be retried" in response.json()["detail"]


def test_no_upload_directory_is_a_503_naming_it(tmp_path):
    client = TestClient(create_app(Services(ingest=StubIngest(), jobs=InMemoryJobStore())))
    response = client.post(
        "/api/documents", files={"file": ("paper.pdf", b"%PDF-1.7", "application/pdf")}
    )
    assert response.status_code == 503
    assert response.json()["component"] == "upload_dir"


# ===========================================================================
# ADR-021 — a moved head fails the commit rather than overwriting it
# ===========================================================================


def build(sources, base_document):
    documents = InMemoryDocumentStore()
    documents.seed(base_document)
    kernel = InvariantKernel(sources, AlwaysRenders())
    loop = CommandLoop(
        planner=Planner(ScriptedPlanner([SHORTEN_PLAN, SHORTEN_PLAN])),
        executor=OperationExecutor(
            sources=sources,
            retrieval=ScriptedRetrieval(default=[]),
            verifier=ScriptedVerifier(default=VerificationLabel.SUPPORTS),
            claims=ScriptedClaims([]),
            embedder=BagOfWordsEmbedder(),
            text_model=ScriptedTextModel(CLEAN_REWRITE),
            fingerprints=FakeFingerprintStore(),
            band=TEST_BAND,
        ),
        kernel=kernel,
    )
    return loop, VersionService(documents, kernel), documents


@pytest.mark.asyncio
async def test_an_approval_against_a_moved_head_is_refused_and_names_the_new_head(
    sources, base_document
):
    loop, versions, documents = build(sources, base_document)
    first = await loop.run(base_document, "shorten the introduction")
    second = await loop.run(base_document, "shorten it again")

    committed = await versions.commit(
        first,
        ApprovalRequest(
            change_set_id=first.change_set_id,
            base_version=first.base_version,
            approved_change_ids=[c.change_id for c in first.changes],
        ),
    )
    assert committed.committed and committed.new_version == 2

    # The second proposal was planned against v1, which is no longer the head.
    with pytest.raises(VersionConflict) as raised:
        await versions.commit(
            second,
            ApprovalRequest(
                change_set_id=second.change_set_id,
                base_version=second.base_version,
                approved_change_ids=[c.change_id for c in second.changes],
            ),
        )

    assert raised.value.current_version == 2
    assert raised.value.base_version == 1
    assert "re-plan" in str(raised.value)
    assert await documents.list_versions("doc-1") == [1, 2], "nothing was written"


@pytest.mark.asyncio
async def test_approving_a_version_the_user_was_not_looking_at_is_refused(sources, base_document):
    """A stale browser tab is the ordinary way this goes wrong."""
    loop, versions, _documents = build(sources, base_document)
    result = await loop.run(base_document, "shorten the introduction")

    with pytest.raises(VersionConflict, match="composed against v7"):
        await versions.commit(
            result,
            ApprovalRequest(
                change_set_id=result.change_set_id,
                base_version=7,
                approved_change_ids=[c.change_id for c in result.changes],
            ),
        )


def test_the_conflict_reaches_the_client_as_a_409_carrying_the_new_head(
    base_document, sources
):
    documents = InMemoryDocumentStore()
    documents.seed(base_document)
    services = Services(
        documents=documents,
        sources=sources,
        render_probe=AlwaysRenders(),
        retrieval=ScriptedRetrieval(default=[]),
        verifier=ScriptedVerifier(default=VerificationLabel.SUPPORTS),
        claims=ScriptedClaims([]),
        embedder=BagOfWordsEmbedder(),
        text_model=ScriptedTextModel(CLEAN_REWRITE),
        structured_model=ScriptedPlanner([SHORTEN_PLAN, SHORTEN_PLAN]),
        fingerprints=FakeFingerprintStore(),
        jobs=InMemoryJobStore(),
        band=TEST_BAND,
    )
    client = TestClient(create_app(services))

    first = client.post("/api/documents/doc-1/commands", json={"command": "shorten"}).json()
    second = client.post("/api/documents/doc-1/commands", json={"command": "shorten again"}).json()

    approve = lambda proposal: client.post(  # noqa: E731
        f"/api/change-sets/{proposal['change_set_id']}/approve",
        json={
            "base_version": proposal["base_version"],
            "approved_change_ids": [c["change"]["change_id"] for c in proposal["changes"]],
        },
    )

    assert approve(first).status_code == 200

    conflict = approve(second)
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["error"] == "version_conflict"
    assert body["current_version"] == 2
    assert body["base_version"] == 1
    assert "Nothing was written" in body["hint"]


def test_an_approval_without_a_base_version_is_rejected_by_the_schema(base_document, sources):
    """Optional would mean "land it on whatever the head happens to be", which is the
    silent lost update ADR-021 exists to refuse."""
    documents = InMemoryDocumentStore()
    documents.seed(base_document)
    services = Services(
        documents=documents,
        sources=sources,
        render_probe=AlwaysRenders(),
        retrieval=ScriptedRetrieval(default=[]),
        verifier=ScriptedVerifier(default=VerificationLabel.SUPPORTS),
        claims=ScriptedClaims([]),
        embedder=BagOfWordsEmbedder(),
        text_model=ScriptedTextModel(CLEAN_REWRITE),
        structured_model=ScriptedPlanner([SHORTEN_PLAN]),
        fingerprints=FakeFingerprintStore(),
        jobs=InMemoryJobStore(),
        band=TEST_BAND,
    )
    client = TestClient(create_app(services))
    proposal = client.post("/api/documents/doc-1/commands", json={"command": "shorten"}).json()

    response = client.post(
        f"/api/change-sets/{proposal['change_set_id']}/approve",
        json={"approved_change_ids": []},
    )
    assert response.status_code == 422


def test_a_command_may_name_the_version_it_was_composed_against(base_document, sources):
    documents = InMemoryDocumentStore()
    documents.seed(base_document)
    services = Services(
        documents=documents,
        sources=sources,
        render_probe=AlwaysRenders(),
        retrieval=ScriptedRetrieval(default=[]),
        verifier=ScriptedVerifier(default=VerificationLabel.SUPPORTS),
        claims=ScriptedClaims([]),
        embedder=BagOfWordsEmbedder(),
        text_model=ScriptedTextModel(CLEAN_REWRITE),
        structured_model=ScriptedPlanner([SHORTEN_PLAN, SHORTEN_PLAN]),
        fingerprints=FakeFingerprintStore(),
        jobs=InMemoryJobStore(),
        band=TEST_BAND,
    )
    client = TestClient(create_app(services))

    explicit = client.post(
        "/api/documents/doc-1/commands", json={"command": "shorten", "base_version": 1}
    )
    assert explicit.status_code == 200
    assert explicit.json()["base_version"] == 1

    # `version` stays accepted so a client mid-flight is not broken by the rename.
    legacy = client.post("/api/documents/doc-1/commands", json={"command": "shorten", "version": 1})
    assert legacy.status_code == 200
    assert legacy.json()["base_version"] == 1
