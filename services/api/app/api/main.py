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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.store import ChangeSetNotFound
from app.agent.versioning import ApprovalError
from app.api.deps import DependencyUnavailable, Services, build_services
from app.api.routes import documents, edits, review
from app.core.contracts import KernelRejection, MissingAPIKeyError, ParseFailure

log = logging.getLogger("app.api")

API_TITLE = "answerthat API"


def create_app(services: Services | None = None) -> FastAPI:
    if services is None:
        services = _boot()

    app = FastAPI(title=API_TITLE, version="1.0.0")
    app.state.services = services

    _install_cors(app, services)
    _install_error_handlers(app)

    app.include_router(documents.router)
    app.include_router(review.router)
    app.include_router(edits.router)

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict:
        bound = {
            name: getattr(services, name) is not None
            for name in (
                "documents", "sources", "render_probe", "exporter", "retrieval",
                "verifier", "claims", "review", "ingest", "style",
                "embedder", "text_model", "structured_model",
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


def _boot() -> Services:
    """Startup. Deliberately unguarded — see the module docstring."""
    try:
        return build_services()
    except MissingAPIKeyError as exc:
        # Not swallowed: re-raised after making the reason unmissable in the logs. The
        # process must not come up.
        log.critical("startup aborted — %s", exc)
        print(f"\nCONFIGURATION ERROR: {exc}\n", file=sys.stderr)
        raise


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
                "hint": "Set the required keys and restart. There is no anonymous mode (ADR-010).",
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


app = None  # populated by `uvicorn app.api.main:asgi`


def asgi() -> FastAPI:
    """Entry point for uvicorn: `uvicorn --factory app.api.main:asgi`."""
    return create_app()


__all__ = ["asgi", "create_app"]
