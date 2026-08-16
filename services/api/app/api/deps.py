"""The composition root.

Every collaborator B3 needs from another agent's package is bound here and nowhere else,
which is what keeps `app/agent/` free of imports from `app/parsing/`, `app/review/`,
`app/providers/`, `app/ir/` and `app/export/`.

Two rules govern this file, both of them HR-3:

* **A dependency that is not available is an error, not a fallback.** There is no stub
  retrieval that returns `[]`, no null renderer that says "sure, that renders". A missing
  collaborator produces a 503 naming exactly what is missing.
* **`app/core/config.py` raising is not something we catch.** HR-2 says the application
  aborts when `OPENALEX_API_KEY` or `OPENAI_API_KEY` is absent (`SEMANTIC_SCHOLAR_API_KEY`
  is optional — ADR-010a). B1's config does the raising; our job is to stay out of its way
  and let it reach the operator.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    conversations: Any = None
    """`app.orchestrator.session.ConversationStore` over the three `chat_*` tables."""
    evidence_index: Any = None
    """`app.orchestrator.index.EvidenceIndex` — embeddings and cosine lookup (ADR-034)."""
    orchestrator: Any = None
    """`app.orchestrator.runtime.Orchestrator` — the agent loop and the confirmation gate."""
    watcher: Any = None
    """`app.orchestrator.watcher.ConversationWatcher` — background jobs → conversation."""
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
    "orchestrator": (
        "the conversational orchestrator is not wired — see the reason logged by "
        "`_bind_orchestrator` at startup, which names the collaborator that was missing. "
        "There is no half-equipped agent: a registry short one tool would present it to the "
        "model and fail at the moment the user said yes"
    ),
    "conversations": "the chat_* conversation tables are not wired (ADR-032)",
    "evidence_index": "the evidence index is not wired (ADR-034)",
    "watcher": (
        "the background-job watcher is not wired, so a finished parse or review would never "
        "reach a conversation"
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

    _bind_ingest(services, settings)
    _bind(services, "style", "app.parsing.style", ("get_style_service",), settings)
    _bind_retrieval(services, settings)
    _bind(services, "verifier", "app.review.verify", ("get_verification_service",), settings)
    _bind(services, "claims", "app.review.claims", ("get_claim_extractor",), settings)
    _bind_review(services, settings)

    from app.api.models import build_model_clients  # noqa: PLC0415

    services.embedder, services.text_model, services.structured_model = build_model_clients(settings)

    # Last, because it needs nearly everything above it.
    _bind_orchestrator(services, settings)
    return services


def _bind_orchestrator(services: Services, settings: Any) -> None:
    """The conversational orchestrator, its conversation store, index and watcher.

    Built explicitly rather than through `_bind()`, and this is the third time this file
    has had to say why (`_bind_ingest`, `_bind_retrieval`): **a factory with an injection
    point cannot go through the generic helper**, which passes only `settings`. The
    orchestrator needs a tool registry, and the tool registry needs the ingest pipeline,
    the document store, the source reader, the style service, the review runner, retrieval,
    the command loop, the version service and the exporter. `_bind()` would hand it
    settings and it would come up holding nothing.

    **A missing collaborator leaves the whole thing unbound.** Not a registry with the
    tools we could build: an agent presented with `export_latex` that fails only once the
    user has said yes is worse than an agent that never offered it, and worse again than a
    503 at the moment the chat is opened. `require("orchestrator")` then names it.
    """
    required = (
        "ingest",
        "documents",
        "sources",
        "style",
        "review",
        "retrieval",
        "exporter",
        "embedder",
        "structured_model",
        "text_model",
        "render_probe",
        "fingerprints",
    )
    missing = [name for name in required if getattr(services, name, None) is None]
    if missing:
        log.error(
            "orchestrator not bound: it needs %s, and %s %s not wired. The chat route will "
            "503 naming this rather than starting a half-equipped agent.",
            ", ".join(required),
            ", ".join(missing),
            "is" if len(missing) == 1 else "are",
        )
        return

    try:
        from app.api.adapters import (  # noqa: PLC0415
            CommandGatewayAdapter,
            ExportGatewayAdapter,
            RetrievalIntrospectorAdapter,
            VersionGatewayAdapter,
        )
        from app.core.db import session_scope  # noqa: PLC0415
        from app.core.llm import get_llm_client  # noqa: PLC0415
        from app.orchestrator.index import EvidenceIndex, PostgresEvidenceRowStore  # noqa: PLC0415
        from app.orchestrator.runtime import Orchestrator  # noqa: PLC0415
        from app.orchestrator.session import PostgresConversationStore  # noqa: PLC0415
        from app.orchestrator.tools import ToolContext  # noqa: PLC0415
        from app.orchestrator.watcher import ConversationWatcher  # noqa: PLC0415
    except ImportError as exc:
        log.warning("orchestrator unavailable: %s", exc)
        return

    services.conversations = PostgresConversationStore(session_scope)
    services.evidence_index = EvidenceIndex(
        rows=PostgresEvidenceRowStore(session_scope),
        # The one embedding model, at the one width (ADR-016). A second would produce
        # vectors that score plausibly against the first's and mean nothing.
        #
        # Read through `require()` rather than off the field directly. The `missing` check
        # above has already established every one of these is bound; `require()` is how
        # that is expressed to a reader and to a type checker, and it keeps the failure —
        # if this ever runs out of order — a named 503 rather than an AttributeError
        # several frames later.
        embedder=services.require("embedder"),
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        batch_size=settings.orchestrator_index_batch,
        text_chars=settings.orchestrator_index_text_chars,
    )

    context = ToolContext(
        ingest=services.require("ingest"),
        documents=services.require("documents"),
        sources=services.require("sources"),
        style=services.require("style"),
        review=services.require("review"),
        retrieval=RetrievalIntrospectorAdapter(services.require("retrieval")),
        commands=CommandGatewayAdapter(services),
        versions=VersionGatewayAdapter(services),
        exporter=ExportGatewayAdapter(services),
        index=services.evidence_index,
        settings=settings,
    )
    services.orchestrator = Orchestrator(
        # The one LLM client, so `converse()` charges the same per-document budget the
        # review and the planner charge, and record/replay covers the chat too (ADR-018).
        model=get_llm_client(settings),
        conversations=services.conversations,
        tool_context=context,
        settings=settings,
    )
    services.watcher = ConversationWatcher(
        orchestrator=services.orchestrator,
        ingest=services.require("ingest"),
        review=services.require("review"),
        documents=services.require("documents"),
        sources=services.require("sources"),
        index=services.evidence_index,
        settings=settings,
    )
    log.info("bound orchestrator, conversations, evidence_index and watcher")


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


def _bind_retrieval(services: Services, settings: Any) -> None:
    """B2's candidate retrieval, which needs B1's document store to read the bibliography.

    The same shape of mistake as `_bind_ingest`, found while tracing it, and it was live:
    `get_retrieval_service(settings, *, document_store=None)` was bound through the generic
    `_bind()`, which passes only settings. With no store, `prime()` returns an empty
    `DocumentContext`, `has_bibliography` is False, and `find_candidates` silently runs two
    of the four strategies — the bibliography-seeded `s2_recommendations` and
    `openalex_graph` never fire. CP-3 requires S2 Recommendations "seeded with the paper's
    own cited works", so this is not a tuning question.

    It degrades exactly the way ADR-010 warns about: fewer candidates, so fewer findings,
    which a reviewer reads as a cleaner paper rather than as a thinner search. Bound
    after `_bind_documents` for the obvious reason.
    """
    try:
        from app.review.retrieval import get_retrieval_service  # noqa: PLC0415
    except ImportError as exc:
        log.warning("retrieval unavailable: %s", exc)
        return

    if services.documents is None:
        # Not a fallback to the unseeded service: retrieval that silently searches with
        # half its strategies is the failure this function exists to prevent. Left
        # unbound, `require("retrieval")` names it in a 503.
        log.error("retrieval not bound: it needs B1's document store to seed candidates")
        return

    services.retrieval = get_retrieval_service(settings, document_store=services.documents)
    log.info("bound retrieval → app.review.retrieval.get_retrieval_service")


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


def _bind_ingest(services: Services, settings: Any) -> None:
    """B1's ingest pipeline, which needs somewhere to persist the IR it produces.

    Bound here rather than through the generic `_bind()` helper for one reason: `_bind()`
    calls the factory with `settings` and nothing else, and `get_ingest_pipeline` has a
    second required collaborator that settings cannot carry. Left unpassed, the pipeline
    parsed the paper, reported `complete` at `version: 1`, and wrote nothing — so
    `/api/documents/{doc_id}/parse` 404'd on a document the user had just watched finish.
    A generic binder is the wrong tool for a factory with an injection point.

    `app/parsing/` must not import `app/core/db`'s session handling, so it takes a factory
    — the same arrangement as `_bind_review` above, and this is again the only place in
    the process that knows both halves. One session per ingest, opened when the parse
    reaches its persist stage and committed by `session_scope` on the way out.
    """
    try:
        from app.core.db import session_scope  # noqa: PLC0415
        from app.ir.store import PostgresDocumentStore  # noqa: PLC0415
        from app.parsing.pipeline import get_ingest_pipeline  # noqa: PLC0415
        from app.parsing.reports import PostgresParseReportStore  # noqa: PLC0415
    except ImportError as exc:
        log.warning("ingest unavailable: %s", exc)
        return

    @asynccontextmanager
    async def store_factory() -> AsyncIterator[Any]:
        async with session_scope() as session:
            yield PostgresDocumentStore(session)

    @asynccontextmanager
    async def report_store_factory() -> AsyncIterator[Any]:
        # A second factory rather than a second use of the first: the parse report is
        # written after the IR version is committed, in its own transaction, so a failure
        # to cache the report cannot roll back the document it describes (ADR-032).
        async with session_scope() as session:
            yield PostgresParseReportStore(session)

    services.ingest = get_ingest_pipeline(
        settings, store_factory=store_factory, report_store_factory=report_store_factory
    )
    log.info("bound ingest → app.parsing.pipeline.get_ingest_pipeline")


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
    services.exporter = LatexExporter(lookup, styles_dir, reader=services.sources)


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
