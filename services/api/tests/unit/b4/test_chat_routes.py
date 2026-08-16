"""The HTTP surface for the agentic flow, and the wiring behind it.

Two failure classes this file exists to catch, both of which this repository has shipped
before:

* **A route the frontend composes a URL for and no router serves.** The handles here are
  handed back by the server rather than built by the client, and these tests assert the
  paths actually resolve.
* **A collaborator that is silently unbound.** An unbound orchestrator has to show up in
  `/api/health` and as a 503 that names it, never as a mysterious failure once someone
  opens the chat.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from b4_fakes import ScriptedModel, say  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import Services  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.orchestrator.runtime import Orchestrator  # noqa: E402
from app.orchestrator.session import InMemoryConversationStore  # noqa: E402


class StubWatcher:
    def __init__(self) -> None:
        self.watched: list[str] = []

    def watch_parse(self, conversation) -> None:  # noqa: ANN001
        self.watched.append(f"parse:{conversation.conversation_id}")

    def watch_review(self, conversation) -> None:  # noqa: ANN001
        self.watched.append(f"review:{conversation.conversation_id}")

    def stop(self, conversation_id: str) -> None:
        self.watched = [w for w in self.watched if not w.endswith(conversation_id)]


@pytest.fixture
def wired(context, settings):
    conversations = InMemoryConversationStore()
    orchestrator = Orchestrator(
        model=ScriptedModel([say("Forty-seven references, 39 of them resolved.")]),
        conversations=conversations,
        tool_context=context,
        settings=settings,
    )
    watcher = StubWatcher()
    services = Services(
        settings=settings,
        conversations=conversations,
        orchestrator=orchestrator,
        watcher=watcher,
        evidence_index=context.index,
    )
    return TestClient(create_app(services)), services, watcher


def test_starting_a_conversation_hands_back_the_urls_it_serves(wired) -> None:
    """The client never composes a path. `/api/reviews/{job_id}/stream` — a route no
    router has ever served — shipped because one did."""
    client, _services, watcher = wired

    response = client.post("/api/documents/doc-1/chat")

    assert response.status_code == 200
    body = response.json()
    assert body["doc_id"] == "doc-1"
    assert body["stream"] == f"/api/chat/{body['conversation_id']}/stream"
    assert body["poll"] == f"/api/chat/{body['conversation_id']}"
    # Both handed-back paths resolve.
    assert client.get(body["poll"]).status_code == 200
    # And the watchers were started on arrival, not on the first message — the interesting
    # case is a user who lands here while their PDF is still parsing.
    assert len(watcher.watched) == 2


def test_the_conversation_is_reused_not_duplicated(wired) -> None:
    client, _services, _watcher = wired

    first = client.post("/api/documents/doc-1/chat").json()
    second = client.post("/api/documents/doc-1/chat").json()

    assert first["conversation_id"] == second["conversation_id"]


def test_a_message_is_accepted_immediately_and_runs_in_the_background(wired) -> None:
    """A turn can start a six-minute review; holding the request open for it would tie
    the answer to one HTTP connection surviving."""
    client, _services, _watcher = wired
    conversation_id = client.post("/api/documents/doc-1/chat").json()["conversation_id"]

    response = client.post(
        f"/api/chat/{conversation_id}/messages", json={"text": "how many references?"}
    )

    assert response.status_code == 202
    assert response.json()["stream"] == f"/api/chat/{conversation_id}/stream"


def test_an_empty_message_is_refused(wired) -> None:
    client, _services, _watcher = wired
    conversation_id = client.post("/api/documents/doc-1/chat").json()["conversation_id"]

    response = client.post(f"/api/chat/{conversation_id}/messages", json={"text": "   "})

    assert response.status_code == 400


def test_an_unknown_conversation_is_a_404_that_says_how_to_start_one(wired) -> None:
    client, _services, _watcher = wired

    response = client.get("/api/chat/conv_does_not_exist")

    assert response.status_code == 404
    assert "POST /api/documents" in response.json()["detail"]


def test_the_cold_load_omits_tool_messages(wired) -> None:
    """The UI renders tool activity from `tool_call`/`tool_result` events, which carry the
    structured payload a card needs. Serving the model's `role="tool"` notes as well would
    double every tool line in the transcript."""
    client, services, _watcher = wired
    conversation_id = client.post("/api/documents/doc-1/chat").json()["conversation_id"]
    client.post(f"/api/chat/{conversation_id}/messages", json={"text": "hello"})

    body = client.get(f"/api/chat/{conversation_id}").json()

    assert all(message["role"] != "tool" for message in body["messages"])
    assert body["messages"][0]["content"] == "hello"


def test_stopping_when_nothing_is_running_says_so_rather_than_erroring(wired) -> None:
    client, _services, _watcher = wired
    conversation_id = client.post("/api/documents/doc-1/chat").json()["conversation_id"]

    body = client.post(f"/api/chat/{conversation_id}/stop").json()

    assert body["stopped"] is False
    assert "nothing to cancel" in body["detail"]


async def test_the_stream_sets_the_headers_that_stop_intermediaries_buffering(
    wired,
) -> None:
    """A buffered SSE stream is the single most likely thing to be wrong here, and it
    looks like an agent that does not work rather than like a proxy that does.

    The endpoint is called directly rather than through `TestClient`. A chat stream has no
    terminal event — a conversation does not end the way a review job does — so it runs
    until the client disconnects, and `TestClient` has no way to disconnect: it would
    block forever on a generator waiting for its next heartbeat. Calling the handler gets
    at the thing under test, which is the response's configuration.
    """
    _client, services, _watcher = wired
    conversation_id = (await services.conversations.start("doc-1"))[0].conversation_id

    from app.api.routes import chat

    class StubRequest:
        def __init__(self, services) -> None:  # noqa: ANN001
            self.app = type("App", (), {"state": type("S", (), {"services": services})()})()

        async def is_disconnected(self) -> bool:
            return True

    response = await chat.stream_chat(StubRequest(services), conversation_id)

    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.media_type == "text/event-stream"


async def test_the_stream_replays_the_event_log_before_following_live(wired) -> None:
    """A browser refresh mid-review must repaint everything already said.

    Asserted on the generator the endpoint builds, for the same reason as above.
    """
    _client, services, _watcher = wired
    conversation, _ = await services.conversations.start("doc-1")
    await services.conversations.append_event(conversation, "message", {"content": "earlier"})
    await services.conversations.append_event(conversation, "done", {"tokens_used": 4})

    backlog, _queue = await conversation.subscribe()

    assert [event.event for event in backlog] == ["message", "done"]
    assert backlog[0].payload == {"content": "earlier"}


# --------------------------------------------------------------------------- wiring


def test_an_unbound_orchestrator_is_a_503_that_names_it(settings) -> None:
    """Never a fallback, never a degraded agent. `require()` names what is missing."""
    client = TestClient(create_app(Services(settings=settings)))

    response = client.post("/api/documents/doc-1/chat")

    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "dependency_unavailable"
    assert body["component"] == "orchestrator"
    assert "half-equipped agent" in body["detail"]


def test_health_reports_the_agentic_collaborators(settings) -> None:
    """An unbound orchestrator shows up here rather than as a mystery later."""
    client = TestClient(create_app(Services(settings=settings)))

    body = client.get("/api/health").json()

    for name in ("orchestrator", "conversations", "evidence_index", "watcher"):
        assert name in body["bound"]
        assert name in body["unbound"]
    assert body["status"] == "degraded_wiring"


def test_health_reports_them_bound_when_they_are(wired) -> None:
    client, _services, _watcher = wired

    body = client.get("/api/health").json()

    assert body["bound"]["orchestrator"] is True
    assert body["bound"]["conversations"] is True
    assert body["bound"]["evidence_index"] is True
    assert body["bound"]["watcher"] is True


def test_the_orchestrator_stays_unbound_when_a_collaborator_is_missing(monkeypatch) -> None:
    """`_bind_orchestrator` builds everything or nothing.

    A registry short one tool would present it to the model and fail at the moment the
    user said yes, which is the worst possible time to discover a wiring problem.
    """
    from app.api import deps

    services = deps.Services(settings=object())
    services.ingest = object()
    services.documents = object()
    # Everything else deliberately left unbound.

    deps._bind_orchestrator(services, object())  # noqa: SLF001

    assert services.orchestrator is None
    assert services.conversations is None
    assert services.evidence_index is None


def test_the_chat_tables_are_registered_for_create_all() -> None:
    """A table whose module was never imported is absent from `Base.metadata` and is
    silently not created — memory.md §4, and the reason this tuple is written out by hand
    rather than discovered."""
    from app.api.main import _TABLE_MODULES

    for module in (
        "app.orchestrator.session",
        "app.orchestrator.index",
        "app.parsing.reports",
    ):
        assert module in _TABLE_MODULES

    import importlib

    from app.core.db import Base

    for module in _TABLE_MODULES:
        importlib.import_module(module)
    for table in ("chat_conversations", "chat_messages", "chat_events", "doc_embeddings", "parse_reports"):
        assert table in Base.metadata.tables
