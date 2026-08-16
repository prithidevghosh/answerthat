"""The four factories B3's edit path and SSE endpoint call, and the job runner.

The property worth pinning hardest is the replayable stream: a review runs for minutes,
a browser reconnects inside that window, and a reconnect that silently loses the findings
already emitted is the same "partial coverage presented as complete" failure ADR-014's
progress counter exists to prevent — just arriving over the network.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.contracts import Claim, MissingAPIKeyError
from app.review import claims as claims_module
from app.review import composition, retrieval, runner, verify
from app.review.runner import ReviewJobRunner


@pytest.fixture(autouse=True)
def _isolate_singletons():
    """The factories are process-wide by design; that must not leak between tests."""
    for module in (composition, retrieval, verify, runner, claims_module):
        module.reset()
    yield
    for module in (composition, retrieval, verify, runner, claims_module):
        module.reset()


# --------------------------------------------------------------------------- factories


def test_every_factory_b3_asked_for_exists_at_the_path_it_named() -> None:
    assert callable(retrieval.get_retrieval_service)
    assert callable(verify.get_verification_service)
    assert callable(claims_module.get_claim_extractor)
    assert callable(runner.get_review_runner)


def test_the_review_runner_factory_refuses_to_guess_its_pipeline() -> None:
    """The pipeline needs B1's DocumentStore, which app/review must not import."""
    with pytest.raises(RuntimeError) as exc:
        runner.get_review_runner()
    assert "composition root" in str(exc.value)


def test_the_provider_bundle_shares_one_limiter_per_provider(monkeypatch) -> None:
    """Two buckets for one key would double our request rate against a per-key limit."""
    from app.core.config import Settings, reset_settings_cache

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "k1")
    monkeypatch.setenv("OPENALEX_API_KEY", "k2")
    monkeypatch.setenv("OPENALEX_MAILTO", "a@b.org")
    reset_settings_cache()

    bundle = composition.ProviderBundle(Settings())
    s2 = bundle.semantic_scholar
    # Graph and Recommendations are different base URLs on the same key.
    assert s2.graph.limiter is s2.recommendations.limiter is s2.limiter
    assert composition.get_providers(Settings()) is not None


def test_a_bundle_with_a_missing_key_raises_rather_than_coming_up_partial(monkeypatch) -> None:
    from app.core.config import Settings, reset_settings_cache

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "k1")
    monkeypatch.setenv("OPENALEX_API_KEY", "k2")
    monkeypatch.setenv("OPENALEX_MAILTO", "")
    reset_settings_cache()

    with pytest.raises(MissingAPIKeyError):
        composition.ProviderBundle(Settings())


# --------------------------------------------------------------------------- retrieval


class _FakeProviders:
    def __init__(self) -> None:
        self.semantic_scholar = _NullS2()
        self.openalex = _NullOpenAlex()
        self.store = _NullStore()


class _NullS2:
    search_pool_available = True

    async def snippet_search(self, query, limit=10):
        return []

    async def recommendations_from(self, seeds, limit=20, negative_paper_ids=None):
        return []


class _NullOpenAlex:
    async def search_works(self, query, limit=10):
        return []

    async def one_hop_expansion(self, ids, limit=50):
        return []


class _NullStore:
    async def warm(self, ids):
        return None

    def get(self, source_id):
        return None


@pytest.fixture
def a_claim() -> Claim:
    return Claim(claim_id="clm_1", text="A claim about the world.", span_id="spn_1", citability=0.9)


async def test_retrieval_reports_which_strategies_actually_ran(a_claim) -> None:
    """Two of four strategies need a bibliography. That reduction is reported, not hidden."""
    service = retrieval.RetrievalService(providers=_FakeProviders())

    await service.find_candidates(a_claim)

    assert service.last_strategies == ("s2_snippet", "openalex_search")


async def test_retrieval_returns_source_ids_the_store_already_holds(a_claim, source_record) -> None:
    """B3 receives keys into the append-only store, never a record it could edit (HR-1)."""
    hit = source_record("src_found", "A paper", "10.1/found")

    class _S2WithHit(_NullS2):
        async def snippet_search(self, query, limit=10):
            class _S:
                source_id, text, record = "src_found", "a passage", hit

            return [_S()]

    providers = _FakeProviders()
    providers.semantic_scholar = _S2WithHit()
    service = retrieval.RetrievalService(providers=providers)

    result = await service.find_candidates(a_claim)

    assert result == ["src_found"]
    assert all(isinstance(source_id, str) for source_id in result)


# --------------------------------------------------------------------------- job runner


def _scripted_pipeline(events, *, fail_with: Exception | None = None):
    class _Pipeline:
        async def stream(self, doc_id, *, section_ids=None):
            for event in events:
                yield event
                await asyncio.sleep(0)
            if fail_with is not None:
                raise fail_with

    return lambda: _Pipeline()


EVENTS = [
    ("progress", {"claims_total": 2, "claims_verified": 0}),
    ("finding", {"finding_id": "fnd_1", "kind": "missing_work"}),
    ("progress", {"claims_total": 2, "claims_verified": 1}),
    ("finding", {"finding_id": "fnd_2", "kind": "missing_work"}),
    # Shaped like the real terminal payload: `stream.py` merges the full stats dict in,
    # which is why `status()` can report final counters from it.
    ("done", {"reason": "complete", "claims_total": 2, "claims_verified": 2}),
]


async def test_a_completed_review_replays_in_full_to_a_late_subscriber() -> None:
    """A browser that connects after the job finished must still see every finding."""
    jobs = ReviewJobRunner(_scripted_pipeline(EVENTS))
    await jobs.run("doc_1")

    replayed = [event async for event in jobs.stream("doc_1")]

    assert [name for name, _ in replayed] == ["progress", "finding", "progress", "finding", "complete"]
    assert jobs.status("doc_1")["status"] == "complete"


async def test_a_completed_review_is_replayed_rather_than_billed_again() -> None:
    """The review screen auto-starts on mount, so every refresh called `start`.

    `start` only skipped work while a job was still *running*, so a finished review was
    re-run from scratch on every page load — a fresh pass over the whole paper against
    the LLM and both providers, discarding findings the user had already read. The job
    holds its event log, so returning it replays the feed for nothing.
    """
    pipeline_runs = []

    def factory():
        pipeline_runs.append(1)
        return _scripted_pipeline(EVENTS)()

    jobs = ReviewJobRunner(factory)
    first = jobs.start("doc_1")
    await jobs._jobs["doc_1"].task

    second = jobs.start("doc_1")
    assert second == first, "the completed job should be handed back, not superseded"
    assert pipeline_runs == [1], "the pipeline must not run a second time"

    replayed = [name for name, _ in [e async for e in jobs.stream("doc_1")]]
    assert replayed.count("finding") == 2, "the user's findings survive the refresh"


async def test_force_reruns_a_completed_review() -> None:
    """Replay must not become a trap: asking for a fresh look is a real request."""
    runs = []

    def factory():
        runs.append(1)
        return _scripted_pipeline(EVENTS)()

    jobs = ReviewJobRunner(factory)
    jobs.start("doc_1")
    await jobs._jobs["doc_1"].task

    second = jobs.start("doc_1", force=True)
    await jobs._jobs["doc_1"].task
    assert runs == [1, 1], "force must actually re-run the pipeline"
    assert jobs._jobs["doc_1"].job_id == second


async def test_a_different_scope_is_a_different_question_and_reruns() -> None:
    runs = []

    def factory():
        runs.append(1)
        return _scripted_pipeline(EVENTS)()

    jobs = ReviewJobRunner(factory)
    jobs.start("doc_1", ["sec_a"])
    await jobs._jobs["doc_1"].task

    jobs.start("doc_1", ["sec_b"])
    await jobs._jobs["doc_1"].task
    assert runs == [1, 1], "a review of another section is not this review"


async def test_a_failed_review_is_retried_rather_than_replayed() -> None:
    """Handing back the same error forever is a dead end, not a cache."""
    from app.core.contracts import ProviderRateLimited

    runs = []

    def factory():
        runs.append(1)
        return _scripted_pipeline(
            [("finding", {"finding_id": "fnd_1"})],
            fail_with=ProviderRateLimited("semantic_scholar throttled"),
        )()

    jobs = ReviewJobRunner(factory)
    jobs.start("doc_1")
    await jobs._jobs["doc_1"].task

    jobs.start("doc_1")
    await jobs._jobs["doc_1"].task
    assert runs == [1, 1], "a failed review must be retried on the next request"


async def test_a_reconnect_mid_review_loses_nothing() -> None:
    """The whole point of buffering: reconnect gets the backlog, then follows live."""
    gate = asyncio.Event()

    class _Pipeline:
        async def stream(self, doc_id, *, section_ids=None):
            yield ("finding", {"finding_id": "fnd_early"})
            await gate.wait()
            yield ("finding", {"finding_id": "fnd_late"})
            yield ("done", {"reason": "complete"})

    jobs = ReviewJobRunner(lambda: _Pipeline())
    jobs.start("doc_1")
    await asyncio.sleep(0.01)  # let the early finding land

    seen: list[tuple[str, dict]] = []

    async def reader():
        async for event in jobs.stream("doc_1"):
            seen.append(event)

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.01)
    gate.set()
    await asyncio.wait_for(task, timeout=2)

    ids = [payload.get("finding_id") for name, payload in seen if name == "finding"]
    assert ids == ["fnd_early", "fnd_late"], "the backlog must precede the live tail"


async def test_the_done_event_is_renamed_to_complete_on_the_wire() -> None:
    """`stream.py` says "done"; B3 frames "complete"."""
    jobs = ReviewJobRunner(_scripted_pipeline([("done", {"reason": "complete"})]))
    await jobs.run("doc_1")
    assert [name for name, _ in jobs._jobs["doc_1"].events] == ["complete"]


async def test_a_pipeline_failure_becomes_a_visible_error_event() -> None:
    """HR-3: a stream that simply stops looks like a review that found less."""
    from app.core.contracts import ProviderRateLimited

    jobs = ReviewJobRunner(
        _scripted_pipeline(
            [("finding", {"finding_id": "fnd_1"})],
            fail_with=ProviderRateLimited("semantic_scholar throttled"),
        )
    )
    await jobs.run("doc_1")

    events = [name for name, _ in jobs._jobs["doc_1"].events]
    assert events == ["finding", "error"]
    status = jobs.status("doc_1")
    assert status["status"] == "failed"
    assert "throttled" in status["error"]


async def test_record_failure_surfaces_a_reason_even_for_an_unstarted_document() -> None:
    jobs = ReviewJobRunner(_scripted_pipeline([]))
    await jobs.record_failure("doc_missing", "GROBID never became healthy")

    status = jobs.status("doc_missing")
    assert status["status"] == "failed"
    assert status["error"] == "GROBID never became healthy"


async def test_streaming_a_review_that_was_never_started_raises() -> None:
    """An empty stream would be indistinguishable from a review that found nothing."""
    jobs = ReviewJobRunner(_scripted_pipeline([]))
    with pytest.raises(KeyError):
        [event async for event in jobs.stream("doc_never")]


async def test_starting_a_running_review_twice_does_not_race_two_pipelines() -> None:
    """Two pipelines against one rate limiter halves the throughput of both."""
    gate = asyncio.Event()
    starts = []

    class _Pipeline:
        async def stream(self, doc_id, *, section_ids=None):
            starts.append(doc_id)
            await gate.wait()
            yield ("done", {"reason": "complete"})

    jobs = ReviewJobRunner(lambda: _Pipeline())
    first = jobs.start("doc_1")
    second = jobs.start("doc_1")
    await asyncio.sleep(0.01)
    gate.set()
    await asyncio.sleep(0.01)

    assert first == second
    assert starts == ["doc_1"]


async def test_status_carries_the_latest_progress_counters() -> None:
    jobs = ReviewJobRunner(_scripted_pipeline(EVENTS))
    await jobs.run("doc_1")

    status = jobs.status("doc_1")
    assert status["claims_verified"] == 2
    assert status["claims_total"] == 2
    assert status["events_emitted"] == 5


def test_status_of_an_unknown_document_says_not_started() -> None:
    jobs = ReviewJobRunner(_scripted_pipeline([]))
    assert jobs.status("doc_x")["status"] == "not_started"


async def test_an_idle_stream_emits_a_heartbeat_rather_than_hanging() -> None:
    """A proxy will drop an SSE connection that goes quiet for long enough."""
    gate = asyncio.Event()

    class _Pipeline:
        async def stream(self, doc_id, *, section_ids=None):
            yield ("progress", {"claims_verified": 0})
            await gate.wait()
            yield ("done", {"reason": "complete"})

    jobs = ReviewJobRunner(lambda: _Pipeline())
    jobs.start("doc_1")
    await asyncio.sleep(0.01)

    seen: list[str] = []

    async def reader():
        async for name, _ in jobs.stream("doc_1", heartbeat_seconds=0.01):
            seen.append(name)

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.wait_for(task, timeout=2)

    assert "heartbeat" in seen
    assert seen[-1] == "complete"
