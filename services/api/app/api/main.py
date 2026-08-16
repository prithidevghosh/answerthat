"""The FastAPI application factory.

**HR-2 lives at the top of this file.** `build_services()` imports `app.core.config`, which
raises `MissingAPIKeyError` when a required key is absent. That exception is not caught
here, not logged-and-continued, and not turned into a banner. It stops the process, and
the operator sees the reason. A generic 500 at request time — or worse, a running service
that returns thin results — is precisely the failure ADR-010 exists to prevent.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.store import ChangeSetNotFound
from app.agent.versioning import ApprovalError, VersionConflict
from app.api.deps import DependencyUnavailable, Services, build_services
from app.api.routes import documents, edits, jobs, review, sources
from app.api.schemas import VersionConflictDetail
from app.core.config import unauthenticated_providers
from app.core.contracts import KernelRejection, MissingAPIKeyError, ParseFailure
from app.core.db import create_all, dispose_engine
from app.core.errors import ExportFailure, IRVersionConflict

log = logging.getLogger("app.api")

API_TITLE = "answerthat API"


def create_app(services: Services | None = None) -> FastAPI:
    # Only the process that wired its own collaborators owns the database. An app handed
    # pre-built services (tests, embedding hosts) gets no schema pass and no engine
    # disposal: it may have been given in-memory stores precisely because there is no
    # Postgres to reach, and connecting to one anyway would fail for the wrong reason.
    owns_database = services is None
    if services is None:
        services = _boot()

    app = FastAPI(title=API_TITLE, version="1.0.0", lifespan=_lifespan_for(owns_database))
    app.state.services = services

    _install_cors(app, services)
    _install_error_handlers(app)

    app.include_router(documents.router)
    app.include_router(review.router)
    app.include_router(edits.router)
    app.include_router(jobs.router)
    app.include_router(sources.router)

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict:
        bound = {
            name: getattr(services, name) is not None
            for name in (
                "documents", "sources", "render_probe", "exporter", "retrieval",
                "verifier", "claims", "review", "ingest", "style",
                "embedder", "text_model", "structured_model", "fingerprints", "jobs",
            )
        }
        missing = sorted(name for name, ok in bound.items() if not ok)
        return {
            "status": "ok" if not missing else "degraded_wiring",
            "bound": bound,
            "unbound": missing,
            "note": (
                "unbound collaborators cause a 503 naming them, never a silent fallback"
                if missing
                else None
            ),
        }

    return app


def _lifespan_for(owns_database: bool):
    """Create the schema before the first request, and let a failure stop the boot.

    There is no Alembic in v1 (ADR-020), so `create_all()` at startup *is* the migration
    story — and nothing was calling it, which is why every write landed on a table that
    did not exist. `create_all()` is idempotent: it issues CREATE TABLE only for what is
    missing, so this is safe on every restart.

    Deliberately unguarded, for the same reason `_boot()` is. A database the API cannot
    reach or cannot create its tables in is not a degraded mode — it is an API that will
    500 on every upload while reporting itself healthy.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if owns_database:
            _import_table_modules()
            await create_all()
            log.info("database schema is present")
        yield
        if owns_database:
            await dispose_engine()

    return lifespan


# Every module that declares a table. A table whose module was never imported is absent
# from `Base.metadata` and is silently not created (memory.md §4). `build_services()` has
# imported all of these by the time the lifespan runs; naming them here is what keeps the
# schema complete if the wiring ever stops binding one of them.
_TABLE_MODULES = (
    "app.api.jobstore",
    "app.ir.store",
    "app.ir.fingerprints",
    "app.providers.cache",
    "app.providers.source_store",
)


def _import_table_modules() -> None:
    """ImportError is tolerated for the same reason `deps.py` tolerates it: during a
    parallel build a package may not have landed yet, and that is already reported there
    as an unbound collaborator. Any other exception is a real fault and propagates."""
    import importlib  # noqa: PLC0415

    for module_path in _TABLE_MODULES:
        try:
            importlib.import_module(module_path)
        except ImportError as exc:
            log.warning("no tables registered from %s: %s", module_path, exc)


def _boot() -> Services:
    """Startup. Deliberately unguarded — see the module docstring."""
    try:
        services = build_services()
    except MissingAPIKeyError as exc:
        # Not swallowed: re-raised after making the reason unmissable in the logs. The
        # process must not come up.
        log.critical("startup aborted — %s", exc)
        print(f"\nCONFIGURATION ERROR: {exc}\n", file=sys.stderr)
        raise

    # Not a warning about a broken configuration — a statement about a supported one.
    # ADR-010a permits running S2 unauthenticated precisely because throttling there is a
    # 429 we raise on; what HR-3 does not permit is leaving the operator to guess which
    # regime they are in.
    for name in unauthenticated_providers():
        log.info("%s not set — using the shared unauthenticated pool (ADR-010a)", name)
    return services


def _install_cors(app: FastAPI, services: Services) -> None:
    """The browser connects to this service directly, including for SSE — see memory.md §5.
    Proxying the review stream through a Next.js route handler buffers it and makes
    streaming look broken, so CORS here is load-bearing rather than convenience."""
    origins = getattr(services.settings, "cors_origins", None) or [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Accel-Buffering", "Content-Type"],
    )


def _install_error_handlers(app: FastAPI) -> None:
    """Every failure gets a named shape and a readable reason. No generic 500s standing in
    for conditions we actually understand (HR-3)."""

    @app.exception_handler(MissingAPIKeyError)
    async def _missing_key(_request: Request, exc: MissingAPIKeyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": "configuration_error",
                "detail": str(exc),
                "hint": (
                    "Set the required keys and restart. These have no anonymous mode "
                    "(ADR-010); SEMANTIC_SCHOLAR_API_KEY is not one of them (ADR-010a)."
                ),
            },
        )

    @app.exception_handler(DependencyUnavailable)
    async def _unavailable(_request: Request, exc: DependencyUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": "dependency_unavailable", "component": exc.what, "detail": exc.detail},
        )

    @app.exception_handler(ChangeSetNotFound)
    async def _no_change_set(_request: Request, exc: ChangeSetNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404, content={"error": "change_set_not_found", "detail": str(exc.args[0])}
        )

    @app.exception_handler(ApprovalError)
    async def _approval(_request: Request, exc: ApprovalError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "approval_invalid", "detail": str(exc)})

    @app.exception_handler(VersionConflict)
    async def _conflict(_request: Request, exc: VersionConflict) -> JSONResponse:
        """ADR-021. The response carries the new head so the client can re-plan against it
        — a conflict the UI cannot act on would just be a dead end with a nicer name."""
        return JSONResponse(
            status_code=409,
            content=VersionConflictDetail(
                doc_id=exc.doc_id,
                base_version=exc.base_version,
                current_version=exc.current_version,
                detail=exc.detail,
            ).model_dump(mode="json"),
        )

    @app.exception_handler(IRVersionConflict)
    async def _ir_conflict(_request: Request, exc: IRVersionConflict) -> JSONResponse:
        """The same condition caught inside B1's store, at the write itself. It reaches
        here only when the pre-checks raced; the shape stays the same so the client has one
        thing to handle."""
        return JSONResponse(
            status_code=409,
            content={
                "error": "version_conflict",
                "detail": str(exc),
                "hint": "Nothing was written. Re-read the head and re-plan (ADR-021).",
            },
        )

    @app.exception_handler(KernelRejection)
    async def _kernel(_request: Request, exc: KernelRejection) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": "kernel_rejection",
                "detail": str(exc),
                "hint": "Nothing was written. The reason above is the kernel's, verbatim.",
            },
        )

    @app.exception_handler(ParseFailure)
    async def _parse(_request: Request, exc: ParseFailure) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "parse_failure", "detail": str(exc)})

    @app.exception_handler(ExportFailure)
    async def _export(_request: Request, exc: ExportFailure) -> JSONResponse:
        """The exporter refuses on conditions it understands precisely — no style chosen,
        a cited source with no CSL record — and says why in its message. Letting those
        reach the client as a bare 500 turned a stated refusal into "something broke",
        which is the same false negative HR-3 is written against. 409: the document is
        fine, the request cannot be satisfied until something is decided."""
        return JSONResponse(
            status_code=409,
            content={
                "error": "export_refused",
                "detail": str(exc),
                "hint": "Nothing was written. The reason above is the exporter's, verbatim.",
            },
        )


app = None  # populated by `uvicorn app.api.main:asgi`


def asgi() -> FastAPI:
    """Entry point for uvicorn: `uvicorn --factory app.api.main:asgi`."""
    return create_app()


__all__ = ["asgi", "create_app"]
