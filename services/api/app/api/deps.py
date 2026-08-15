"""The composition root.

Every collaborator B3 needs from another agent's package is bound here and nowhere else,
which is what keeps `app/agent/` free of imports from `app/parsing/`, `app/review/`,
`app/providers/`, `app/ir/` and `app/export/`.

Two rules govern this file, both of them HR-3:

* **A dependency that is not available is an error, not a fallback.** There is no stub
  retrieval that returns `[]`, no null renderer that says "sure, that renders". A missing
  collaborator produces a 503 naming exactly what is missing.
* **`app/core/config.py` raising is not something we catch.** HR-2 says the application
  aborts when `SEMANTIC_SCHOLAR_API_KEY` or `OPENALEX_API_KEY` is absent. B1's config does
  the raising; our job is to stay out of its way and let it reach the operator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.agent.executor import OperationExecutor
from app.agent.kernel import DEFAULT_SIMILARITY_THRESHOLD, InvariantKernel
from app.agent.loop import CommandLoop
from app.agent.planner import Planner
from app.agent.ports import (
    ClaimExtractor,
    DocumentStore,
    Embedder,
    Exporter,
    RenderProbe,
    RetrievalService,
    ReviewRunner,
    SourceReader,
    StructuredModel,
    TextModel,
    VerificationService,
)
from app.agent.store import ChangeSetStore
from app.agent.versioning import VersionService

log = logging.getLogger("app.api.deps")


class DependencyUnavailable(RuntimeError):
    """A collaborator this route needs has not been wired. Rendered as a 503 that names it."""

    def __init__(self, what: str, detail: str) -> None:
        super().__init__(f"{what} is not available: {detail}")
        self.what = what
        self.detail = detail


@dataclass
class Services:
    """Everything the API layer is allowed to reach for.

    Fields are Optional because this service boots while B1 and B2 are still landing their
    packages. Optional does not mean degradable: `require()` turns a missing collaborator
    into a loud, specific failure at the moment a route needs it.
    """

    documents: DocumentStore | None = None
    sources: SourceReader | None = None
    render_probe: RenderProbe | None = None
    exporter: Exporter | None = None
    retrieval: RetrievalService | None = None
    verifier: VerificationService | None = None
    claims: ClaimExtractor | None = None
    review: ReviewRunner | None = None
    ingest: Any = None
    style: Any = None
    embedder: Embedder | None = None
    text_model: TextModel | None = None
    structured_model: StructuredModel | None = None
    jobs: Any = None
    change_sets: ChangeSetStore | None = None
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD
    settings: Any = None

    # -------------------------------------------------------------- accessors

    def require(self, name: str) -> Any:
        value = getattr(self, name, None)
        if value is None:
            raise DependencyUnavailable(
                name,
                _MISSING_HINTS.get(
                    name, f"no implementation was bound for {name!r} at application start"
                ),
            )
        return value

    def kernel(self) -> InvariantKernel:
        return InvariantKernel(
            self.require("sources"),
            self.require("render_probe"),
            similarity_threshold=self.similarity_threshold,
        )

    def executor(self) -> OperationExecutor:
        return OperationExecutor(
            sources=self.require("sources"),
            retrieval=self.require("retrieval"),
            verifier=self.require("verifier"),
            claims=self.require("claims"),
            embedder=self.require("embedder"),
            text_model=self.require("text_model"),
            similarity_threshold=self.similarity_threshold,
        )

    def command_loop(self) -> CommandLoop:
        return CommandLoop(
            planner=Planner(self.require("structured_model")),
            executor=self.executor(),
            kernel=self.kernel(),
        )

    def versions(self) -> VersionService:
        return VersionService(self.require("documents"), self.kernel())

    def change_set_store(self) -> ChangeSetStore:
        if self.change_sets is None:
            self.change_sets = ChangeSetStore()
        return self.change_sets


_MISSING_HINTS = {
    "documents": "B1's IR version store (app/ir) is not wired — see memory.md §5 IR-1",
    "render_probe": "B1's Pandoc render probe (app/export) is not wired — see memory.md §5 IR-2",
    "exporter": "B1's LaTeX exporter (app/export) is not wired — see memory.md §5 IR-2",
    "sources": "B2's read-only source_store view is not wired — see memory.md §5 IR-3",
    "retrieval": "B2's candidate retrieval is not wired — see memory.md §5 IR-4",
    "verifier": "B2's quote-backed verifier is not wired — see memory.md §5 IR-4",
    "claims": "B2's claim extractor is not wired — see memory.md §5 IR-4",
    "review": "B2's streaming review runner is not wired — see memory.md §5 IR-5",
    "ingest": "B1's ingest pipeline is not wired — see memory.md §5 IR-1",
    "style": "B1's style detection is not wired — see memory.md §5 IR-6",
    "embedder": "no embedding backend was configured",
    "text_model": "no text model was configured",
    "structured_model": "no planner model was configured",
    "jobs": "the arq job queue is not connected",
}


def build_services() -> Services:
    """Production wiring.

    `app/core/config.py` is imported at the top of this function, unguarded and on purpose:
    if a required key is missing it raises `MissingAPIKeyError`, and that exception is meant
    to reach the operator and stop the process (HR-2 / ADR-010). Wrapping this in a
    try/except would reintroduce exactly the degraded mode ADR-010 exists to forbid.
    """
    from app.core.config import get_settings  # noqa: PLC0415 — must raise, must not be cached away

    settings = get_settings()

    services = Services(settings=settings, change_sets=ChangeSetStore())
    services.similarity_threshold = getattr(
        settings, "reattachment_threshold", DEFAULT_SIMILARITY_THRESHOLD
    )

    _bind(services, "documents", "app.ir.store", "get_document_store", settings)
    _bind(services, "ingest", "app.parsing.pipeline", "get_ingest_pipeline", settings)
    _bind(services, "style", "app.parsing.style", "get_style_service", settings)
    _bind(services, "render_probe", "app.export.pandoc", "get_render_probe", settings)
    _bind(services, "exporter", "app.export.latex", "get_exporter", settings)
    _bind(services, "sources", "app.providers.store", "get_source_reader", settings)
    _bind(services, "retrieval", "app.review.retrieval", "get_retrieval_service", settings)
    _bind(services, "verifier", "app.review.verify", "get_verification_service", settings)
    _bind(services, "claims", "app.review.claims", "get_claim_extractor", settings)
    _bind(services, "review", "app.review.runner", "get_review_runner", settings)

    from app.api.models import build_model_clients  # noqa: PLC0415

    services.embedder, services.text_model, services.structured_model = build_model_clients(settings)
    return services


def _bind(services: Services, field: str, module_path: str, factory_name: str, settings: Any) -> None:
    """Bind one collaborator, tolerating only the specific case of "not committed yet".

    An ImportError here means another agent's package has not landed. That is a real,
    temporary state during a parallel build, and the right response is to leave the field
    unbound so `require()` can name it precisely later — not to substitute a stub. Any
    other exception is a genuine fault in that package and is allowed to propagate.
    """
    try:
        module = __import__(module_path, fromlist=[factory_name])
    except ImportError as exc:
        log.warning("%s unavailable: %s (%s)", field, module_path, exc)
        return

    factory = getattr(module, factory_name, None)
    if factory is None:
        log.warning("%s: %s has no %s()", field, module_path, factory_name)
        return

    setattr(services, field, factory(settings))
    log.info("bound %s → %s.%s", field, module_path, factory_name)


__all__ = ["DependencyUnavailable", "Services", "build_services"]
