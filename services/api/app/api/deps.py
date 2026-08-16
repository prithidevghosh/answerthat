"""The composition root.

Every collaborator B3 needs from another agent's package is bound here and nowhere else,
which is what keeps `app/agent/` free of imports from `app/parsing/`, `app/review/`,
`app/providers/`, `app/ir/` and `app/export/`.

Two rules govern this file, both of them HR-3:

* **A dependency that is not available is an error, not a fallback.** There is no stub
  retrieval that returns `[]`, no null renderer that says "sure, that renders". A missing
  collaborator produces a 503 naming exactly what is missing.
* **`app/core/config.py` raising is not something we catch.** HR-2 says the application
  aborts when `SEMANTIC_SCHOLAR_API_KEY`, `OPENALEX_API_KEY` or `OPENAI_API_KEY` is absent.
  B1's config does the raising; our job is to stay out of its way and let it reach the
  operator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.executor import OperationExecutor
from app.agent.kernel import InvariantKernel
from app.agent.loop import CommandLoop
from app.agent.planner import Planner
from app.agent.ports import (
    ClaimExtractor,
    DocumentStore,
    Embedder,
    Exporter,
    FingerprintStore,
    RenderProbe,
    RetrievalService,
    ReviewRunner,
    SourceReader,
    StructuredModel,
    TextModel,
    VerificationService,
)
from app.agent.store import ChangeSetStore
from app.agent.thresholds import ReattachmentBand
from app.agent.versioning import VersionService
from app.api.jobstore import JobStore

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
    fingerprints: FingerprintStore | None = None
    jobs: JobStore | None = None
    upload_dir: Path | None = None
    """Where uploaded PDFs land (ADR-022). Required to accept an upload at all: without
    the bytes on disk, a crashed ingest cannot be retried and the paper is simply gone."""
    change_sets: ChangeSetStore | None = None
    band: ReattachmentBand | None = None
    """`REATTACH_ACCEPT` / `REATTACH_FLAG_FLOOR`, read from config at boot (ADR-024). No
    default here on purpose: a default in this file would be a threshold living outside
    `app/core/config.py`."""
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
        """Pure, and holding only a read-only source view and a render probe. It takes no
        threshold: the number an anchor was judged against travels on its
        `ReattachmentRecord` instead (ADR-024)."""
        return InvariantKernel(self.require("sources"), self.require("render_probe"))

    def executor(self) -> OperationExecutor:
        return OperationExecutor(
            sources=self.require("sources"),
            retrieval=self.require("retrieval"),
            verifier=self.require("verifier"),
            claims=self.require("claims"),
            embedder=self.require("embedder"),
            text_model=self.require("text_model"),
            fingerprints=self.require("fingerprints"),
            band=self.require("band"),
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
    "fingerprints": "B1's anchor_fingerprints side table (app/ir/fingerprints.py) is not wired",
    "band": "the reattachment band was not read from config — see ADR-024",
    "jobs": "the arq job queue is not connected",
    "upload_dir": (
        "UPLOAD_DIR is not configured, so an uploaded PDF has nowhere to be written and a "
        "failed ingest could never be retried (ADR-022)"
    ),
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

    services = Services(
        settings=settings,
        change_sets=ChangeSetStore(),
        band=ReattachmentBand.from_settings(settings),
        upload_dir=Path(settings.upload_dir),
    )

    _bind_sources(services, settings)
    _bind_documents(services, settings)
    _bind_fingerprints(services, settings)
    _bind_jobs(services, settings)
    _bind_export(services, settings)

    _bind(services, "ingest", "app.parsing.pipeline", ("get_ingest_pipeline",), settings)
    _bind(services, "style", "app.parsing.style", ("get_style_service",), settings)
    _bind(services, "retrieval", "app.review.retrieval", ("get_retrieval_service",), settings)
    _bind(services, "verifier", "app.review.verify", ("get_verification_service",), settings)
    _bind(services, "claims", "app.review.claims", ("get_claim_extractor",), settings)
    _bind_review(services, settings)

    from app.api.models import build_model_clients  # noqa: PLC0415

    services.embedder, services.text_model, services.structured_model = build_model_clients(settings)
    return services


def _bind_sources(services: Services, settings: Any) -> None:
    """B2's append-only store, behind the read-only `SourceReader` port.

    We take the concrete `PostgresSourceStore` and wrap it, rather than passing it through:
    the adapter exposes `get`/`has`/`warm` and nothing else, so `put` is not reachable from
    anywhere in `app/agent/` even by accident (HR-1).
    """
    try:
        from app.api.adapters import SourceReaderAdapter
        from app.providers.source_store import PostgresSourceStore
    except ImportError as exc:
        log.warning("sources unavailable: %s", exc)
        return
    services.sources = SourceReaderAdapter(PostgresSourceStore())


def _bind_documents(services: Services, settings: Any) -> None:
    try:
        from app.api.adapters import DocumentStoreAdapter
        from app.core.db import session_scope
        from app.ir.store import PostgresDocumentStore
    except ImportError as exc:
        log.warning("documents unavailable: %s", exc)
        return
    services.documents = DocumentStoreAdapter(PostgresDocumentStore, session_scope)


def _bind_fingerprints(services: Services, settings: Any) -> None:
    """B1's `anchor_fingerprints` side table (ADR-017), behind B3's port."""
    try:
        from app.api.adapters import FingerprintStoreAdapter
        from app.core.db import session_scope
        from app.ir.fingerprints import PostgresFingerprintStore
    except ImportError as exc:
        log.warning("fingerprints unavailable: %s", exc)
        return
    services.fingerprints = FingerprintStoreAdapter(
        PostgresFingerprintStore,
        session_scope,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )


def _bind_review(services: Services, settings: Any) -> None:
    """B2's review job runner, which needs B1's document store injected.

    `app/review/` must not import `app/ir/`, so B2 takes a factory instead — and this is
    the only place in the process that knows both halves. The factory is lazy on purpose:
    it is called per job, so a review started after the document store was bound picks it
    up rather than capturing whatever was there at boot.
    """
    try:
        from app.review.composition import build_review_runner  # noqa: PLC0415
        from app.review.runner import get_review_runner  # noqa: PLC0415
    except ImportError as exc:
        log.warning("review unavailable: %s", exc)
        return

    def pipeline_factory() -> Any:
        return build_review_runner(services.require("documents"), settings)

    services.review = get_review_runner(settings, review_runner_factory=pipeline_factory)
    log.info("bound review → app.review.runner.get_review_runner")


def _bind_jobs(services: Services, settings: Any) -> None:
    """`agent_jobs` (ADR-020, ADR-022). B3's own table, so this is a hard requirement —
    an unbound job store means a dead worker has nowhere to be reported."""
    from app.api.jobstore import PostgresJobStore  # noqa: PLC0415
    from app.core.db import session_scope  # noqa: PLC0415

    services.jobs = PostgresJobStore(session_scope)


def _bind_export(services: Services, settings: Any) -> None:
    if services.sources is None:
        log.warning("export unavailable: it needs the source store for CSL lookup")
        return
    try:
        from app.api.adapters import LatexExporter, PandocRenderProbe, csl_lookup_for
    except ImportError as exc:
        log.warning("export unavailable: %s", exc)
        return

    lookup = csl_lookup_for(services.sources)
    styles_dir = getattr(settings, "csl_styles_dir", None)
    services.render_probe = PandocRenderProbe(lookup, styles_dir)
    services.exporter = LatexExporter(lookup, styles_dir)


def _bind(
    services: Services, field: str, module_path: str, factory_names: tuple[str, ...], settings: Any
) -> None:
    """Bind one collaborator, tolerating only the specific case of "not committed yet".

    An ImportError here means another agent's package has not landed. That is a real,
    temporary state during a parallel build, and the right response is to leave the field
    unbound so `require()` can name it precisely later — not to substitute a stub. Any
    other exception is a genuine fault in that package and is allowed to propagate.
    """
    try:
        module = __import__(module_path, fromlist=list(factory_names))
    except ImportError as exc:
        log.warning("%s unavailable: %s (%s)", field, module_path, exc)
        return

    for factory_name in factory_names:
        factory = getattr(module, factory_name, None)
        if factory is not None:
            setattr(services, field, factory(settings))
            log.info("bound %s → %s.%s", field, module_path, factory_name)
            return

    log.warning("%s: %s has none of %s", field, module_path, list(factory_names))


__all__ = ["DependencyUnavailable", "Services", "build_services"]
