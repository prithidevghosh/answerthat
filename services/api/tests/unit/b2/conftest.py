"""Fixtures for B2's unit tests.

Everything here builds a provider against a *mocked transport*, never a relaxed provider.
OpenAlex and Crossref require credentials at construction (HR-2), so the tests pass fake
ones and stub the wire — the only sanctioned way to run without credentials, and
deliberately harder than adding an anonymous mode would have been. S2's key is optional
(ADR-010a) but supplied here anyway, so that the authenticated path stays the one under
test by default; `test_semantic_scholar.py` builds its own keyless provider for the
anonymous cases.

Payloads and helpers are exposed as fixtures rather than importable constants so that the
test package needs no `__init__.py` and no sibling imports.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app.providers.cache import InMemoryResponseCache
from app.providers.ratelimit import TokenBucket
from app.providers.source_store import InMemorySourceStore

FAKE_S2_KEY = "test-s2-key"
FAKE_OPENALEX_KEY = "test-openalex-key"
FAKE_OPENAI_KEY = "test-openai-key"
FAKE_MAILTO = "tests@answerthat.local"


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch) -> None:
    """Every key present, all of them fake. HR-2 / ADR-010.

    Thresholds are read from `Settings` at construction — `CITABILITY_MIN`, `RERANK_KEEP`,
    `VERIFY_KEEP` (ADR-024) — and `Settings` refuses to exist without the required keys.
    So the suite supplies keys rather than relaxing the check, which is the same trade the
    provider tests make with their stubbed transports: fake the credential, never the
    enforcement.

    `LLM_MODE=replay` on top, so a test that reached a real model call fails on a missing
    recording instead of quietly billing someone (ADR-018).
    """
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", FAKE_S2_KEY)
    monkeypatch.setenv("OPENALEX_API_KEY", FAKE_OPENALEX_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI_KEY)
    monkeypatch.setenv("OPENALEX_MAILTO", FAKE_MAILTO)
    monkeypatch.setenv("LLM_MODE", "replay")

    from app.core.config import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()


class RecordingTransport(httpx.MockTransport):
    """A mock transport that also records what was actually sent.

    Several tests assert on the request rather than the response — that a key was
    attached, that `mailto` was present, that ids were sorted before sending. Those are
    the properties a mocked-response-only test would miss.
    """

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []

        def recording(request: httpx.Request) -> httpx.Response:
            request.read()
            self.requests.append(request)
            return handler(request)

        super().__init__(recording)

    def last_body(self) -> dict:
        return json.loads(self.requests[-1].content or b"{}")

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def route_by_path(routes: dict[str, object], *, default_status: int = 404):
    """Build a handler that answers by URL path prefix.

    A path that is not in `routes` answers 404 on purpose: an unexpected call should show
    up as a failing assertion, not as a silently mocked success.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        for path, payload in routes.items():
            if request.url.path.startswith(path):
                if isinstance(payload, httpx.Response):
                    return payload
                return httpx.Response(200, json=payload)
        return httpx.Response(default_status, json={"error": f"no route for {request.url.path}"})

    return handler


@pytest.fixture
def cache() -> InMemoryResponseCache:
    return InMemoryResponseCache()


@pytest.fixture
def store() -> InMemorySourceStore:
    return InMemorySourceStore()


@pytest.fixture
def fast_limiter() -> TokenBucket:
    """The real limiter, wound up so tests are not paced at 1 rps.

    Still a `TokenBucket`, so the queueing and raising behaviour under test is the
    production class rather than a no-op stand-in.
    """
    return TokenBucket(1000.0, capacity=1000.0, name="test")


@pytest.fixture
def transport_for() -> Callable[..., RecordingTransport]:
    """`transport_for(routes)` or `transport_for(handler=fn)`."""

    def build(routes: dict[str, object] | None = None, *, handler=None) -> RecordingTransport:
        return RecordingTransport(handler or route_by_path(routes or {}))

    return build


# --------------------------------------------------------------------------- payloads


def _attention_paper() -> dict:
    return {
        "paperId": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
        "corpusId": 13756489,
        "externalIds": {"DOI": "10.5555/3295222.3295349", "ArXiv": "1706.03762"},
        "url": "https://www.semanticscholar.org/paper/204e3073870fae3d05bcbc2f6a8e263d9b72e776",
        "title": "Attention Is All You Need",
        "abstract": (
            "The dominant sequence transduction models are based on complex recurrent or "
            "convolutional neural networks. We propose a new simple network architecture, "
            "the Transformer, based solely on attention mechanisms."
        ),
        "venue": "Neural Information Processing Systems",
        "publicationVenue": {"name": "Neural Information Processing Systems", "type": "conference"},
        "year": 2017,
        "authors": [
            {"authorId": "1", "name": "Ashish Vaswani"},
            {"authorId": "2", "name": "Noam Shazeer"},
            {"authorId": "3", "name": "Jakob Uszkoreit"},
        ],
        "journal": {"name": "NeurIPS", "pages": "5998-6008"},
        "publicationTypes": ["Conference"],
        "tldr": {"text": "A new architecture based only on attention."},
        "citationCount": 100000,
    }


def _no_abstract_paper() -> dict:
    """Publisher licensing means a meaningful fraction of S2 records look like this.

    The mandatory fallback chain exists because of them, and `unavailable` is the honest
    end of it.
    """
    return {
        "paperId": "abc123",
        "corpusId": 999,
        "externalIds": {"DOI": "10.1000/no-abstract"},
        "url": "https://www.semanticscholar.org/paper/abc123",
        "title": "A Paper Whose Abstract Is Not Licensed To Us",
        "abstract": None,
        "year": 2021,
        "authors": [{"authorId": "9", "name": "Ada van der Berg"}],
        "publicationTypes": ["JournalArticle"],
        "tldr": None,
    }


def _openalex_work() -> dict:
    return {
        "id": "https://openalex.org/W2741809807",
        "doi": "https://doi.org/10.7717/peerj.4375",
        "title": "The state of OA",
        "display_name": "The state of OA",
        "publication_year": 2018,
        "type": "article",
        "cited_by_count": 900,
        "ids": {
            "openalex": "https://openalex.org/W2741809807",
            "doi": "https://doi.org/10.7717/peerj.4375",
        },
        "primary_location": {
            "source": {"display_name": "PeerJ", "host_organization_name": "PeerJ"},
            "landing_page_url": "https://peerj.com/articles/4375",
        },
        "biblio": {"volume": "6", "issue": None, "first_page": "e4375", "last_page": "e4375"},
        "authorships": [
            {"author": {"display_name": "Heather Piwowar"}, "raw_author_name": "Heather Piwowar"},
            {"author": {"display_name": "Jason Priem"}, "raw_author_name": "Jason Priem"},
        ],
        "open_access": {"is_oa": True},
        # The whole point: there is no plain-abstract field, only this.
        "abstract_inverted_index": {
            "Despite": [0],
            "growing": [1],
            "interest": [2],
            "in": [3],
            "open": [4],
            "access,": [5],
            "we": [6],
            "estimate": [7],
            "the": [8],
            "prevalence": [9],
            "of": [10],
            "OA": [11],
            "articles.": [12],
        },
    }


@pytest.fixture
def attention_paper() -> dict:
    return _attention_paper()


@pytest.fixture
def no_abstract_paper() -> dict:
    return _no_abstract_paper()


@pytest.fixture
def openalex_work() -> dict:
    return _openalex_work()


@pytest.fixture
def source_record():
    """Build a stored-shaped `SourceRecord` for tests of the review layer's pure logic.

    Constructed directly rather than through an adapter: these tests never touch
    `source_store.put()`, whose guards have their own tests in `test_source_store_hr1.py`.
    """
    from app.core.contracts import AbstractSource, Provenance, SourceRecord

    def build(source_id: str, title: str, doi: str | None = None, abstract: str | None = None):
        csl = {"title": title, "type": "article-journal"}
        if doi:
            csl["DOI"] = doi
        return SourceRecord(
            source_id=source_id,
            csl=csl,
            provenance=Provenance(
                provider="semantic_scholar",
                endpoint="/paper/search",
                retrieved_at="2026-08-15T10:00:00+00:00",
                external_url=f"https://doi.org/{doi}" if doi else "https://example.org/x",
            ),
            abstract=abstract,
            abstract_source=AbstractSource.S2 if abstract else AbstractSource.UNAVAILABLE,
        )

    return build
