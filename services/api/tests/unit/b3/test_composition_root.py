"""`build_services()` against the real packages — no fakes below the composition root.

Every other test in this directory substitutes a double for B1 and B2. That is right for
testing B3's logic and useless for testing B3's *wiring*, which is the half that has
actually broken twice:

* `get_review_runner()` requires a pipeline factory, and without it the API aborted at
  boot with a `RuntimeError` from B2's module. Nothing caught it, because no unit test
  ever called `build_services()`.
* the factory is **lazy** — it runs per job, not at boot — so `services.review is not
  None` proves only that a callable was stored, not that calling it produces anything.
  A binding can be present, non-None, and dead.

So these tests call the factory and inspect what comes out. Construction is pure object
assembly: no HTTP, no model call, no database connection. The keys are fake and
`LLM_MODE=replay` means a stray model call would fail on a missing recording rather than
reaching the network (ADR-018).
"""

from __future__ import annotations

import inspect

import pytest


@pytest.fixture
def wired(monkeypatch):
    """A real `Services` from the real composition root, singletons reset around it."""
    import app.core.config as config
    import app.core.llm as llm
    import app.review.claims as claims
    import app.review.composition as composition
    import app.review.retrieval as retrieval
    import app.review.runner as runner
    import app.review.verify as verify
    from app.api.deps import build_services

    for name in ("SEMANTIC_SCHOLAR_API_KEY", "OPENALEX_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, f"fake-{name.lower()}")
    monkeypatch.setenv("OPENALEX_MAILTO", "tests@example.invalid")
    monkeypatch.setenv("LLM_MODE", "replay")

    modules = (composition, runner, retrieval, verify, claims)

    def clear() -> None:
        config.reset_settings_cache()
        llm.reset_llm_client()
        for module in modules:
            module.reset()

    clear()
    try:
        yield build_services()
    finally:
        # These are process-wide singletons on purpose — the S2 rate limiter must be
        # shared — which means they leak into every test that runs after this one.
        clear()


def test_the_review_runner_is_bound_and_its_factory_actually_builds_a_pipeline(wired):
    """IR-5, closed. The assertion that matters is the second one: a lazy factory that
    raises when called is indistinguishable at boot from one that works."""
    runner = wired.require("review")
    assert type(runner).__name__ == "ReviewJobRunner"

    pipeline = runner._factory()
    assert type(pipeline).__name__ == "ReviewRunner"


def test_the_pipeline_gets_the_document_store_b3_bound(wired):
    """`app/review/` must not import `app/ir/`, so this is the only place the two halves
    meet. If they meet on different objects, a review reads a different document than the
    one being edited."""
    pipeline = wired.require("review")._factory()
    assert pipeline.documents is wired.documents


def test_the_factory_is_lazy_rather_than_captured_at_boot(wired):
    """A review started after the document store was rebound must pick it up."""
    runner = wired.require("review")
    assert runner._factory() is not runner._factory()


def test_the_prefilter_is_wired_because_omitting_it_costs_nothing_visible(wired):
    """ADR-015's cost control is the cascade. `ReviewRunner` treats `prefilter` as
    optional, and a runner without one produces identical findings at several times the
    per-claim spend — which is the pressure that later gets answered by economising on
    verification instead. Nothing fails when it is missing, so a test has to say so."""
    pipeline = wired.require("review")._factory()
    assert pipeline.prefilter is not None
    for stage in ("extractor", "candidates", "reranker", "verifier", "store"):
        assert getattr(pipeline, stage) is not None, f"{stage} is not wired"


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("start", {"doc_id": "doc-1", "section_ids": None}),
        ("status", {"doc_id": "doc-1"}),
        ("stream", {"doc_id": "doc-1"}),
    ],
)
def test_the_review_routes_calls_bind_against_b2s_real_signatures(wired, method, kwargs):
    """The exact calls `app/api/routes/review.py` makes.

    B2's `start` and `status` are **synchronous** — the work is launched with
    `asyncio.create_task`, not handed to arq — so the routes go through `maybe_await`.
    Every double in this directory is async, which is precisely why the mismatch survived
    until it was exercised against the real object.
    """
    bound = getattr(wired.require("review"), method)
    inspect.signature(bound).bind(**kwargs)


def test_start_and_status_are_sync_and_stream_is_an_async_generator(wired):
    """Pinned deliberately: if B2 moves to arq these flip to coroutines, `maybe_await`
    absorbs it, and this test is the note explaining why that shim exists."""
    runner = wired.require("review")
    assert not inspect.iscoroutinefunction(runner.start)
    assert not inspect.iscoroutinefunction(runner.status)
    assert inspect.isasyncgenfunction(runner.stream)


def test_every_collaborator_the_api_needs_is_bound(wired):
    """A 503 naming a missing collaborator is the designed behaviour at request time, but
    at boot, with every package present, nothing should be unbound."""
    required = (
        "documents", "sources", "render_probe", "exporter", "retrieval", "verifier",
        "claims", "review", "ingest", "style", "embedder", "text_model",
        "structured_model", "fingerprints", "jobs", "band", "upload_dir",
    )
    unbound = [name for name in required if getattr(wired, name) is None]
    assert unbound == []
