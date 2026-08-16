"""Upload, parse status, IR retrieval, versions, style, export."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from app.api.adapters import maybe_await
from app.api.deps import Services
from app.api.schemas import (
    ExportManifest,
    JobAccepted,
    ParseStatus,
    PlaceholderCount,
    RevertRequest,
    StyleResponse,
    StyleSelection,
    VersionInfo,
)
from app.core.contracts import Document

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def services(request: Request) -> Services:
    return request.app.state.services


async def load_document(svc: Services, doc_id: str, version: int | None = None) -> Document:
    store = svc.require("documents")
    document = await store.get(doc_id, version)
    if document is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"document {doc_id!r}"
                + (f" version {version}" if version is not None else "")
                + " does not exist"
            ),
        )
    return document


@router.post("", response_model=JobAccepted, status_code=202)
async def upload(request: Request, file: UploadFile) -> JobAccepted:
    """The first screen of the product posts here. Returns a job id immediately; parsing
    runs in the background because GROBID plus arbitration takes far longer than a request."""
    svc = services(request)
    ingest = svc.require("ingest")

    if file.content_type not in {"application/pdf", "application/x-pdf", None}:
        raise HTTPException(
            status_code=415,
            detail=f"expected a PDF, received {file.content_type!r}",
        )

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"the PDF is {len(payload) // 1_048_576} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB",
        )

    doc_id = f"doc-{uuid.uuid4().hex[:12]}"
    stored_at = _persist(svc, doc_id, payload)

    job_id = await maybe_await(
        ingest.enqueue(
            doc_id=doc_id, filename=file.filename or f"{doc_id}.pdf", payload=payload
        )
    )
    # Recorded before anything can go wrong with it. A job that exists only inside a
    # worker's memory cannot be reported as failed once that worker is gone (ADR-022).
    if svc.jobs is not None:
        await svc.jobs.create(
            job_id=job_id, kind="ingest", doc_id=doc_id, upload_path=str(stored_at)
        )

    return JobAccepted(
        job_id=job_id,
        doc_id=doc_id,
        poll=f"/api/documents/{doc_id}/parse-status",
    )


def _persist(svc: Services, doc_id: str, payload: bytes) -> Path:
    """Write the PDF to the mounted volume before the ingest starts (ADR-022).

    Before, not after: the job may crash, and a retry needs the bytes. Holding them only
    in the worker's memory means a failed parse is unrecoverable and the researcher is
    asked to upload their paper again for a reason that is our fault.

    A write failure is a failed upload, not a warning. Accepting the file and returning a
    job id we cannot honour would be the most misleading possible answer.
    """
    directory = Path(svc.require("upload_dir"))
    path = directory / f"{doc_id}.pdf"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    except OSError as exc:
        raise HTTPException(
            status_code=507,
            detail=(
                f"could not write the upload to {path}: {exc}. The file was not accepted — "
                f"an ingest whose source bytes are gone cannot be retried, so this fails now "
                f"rather than several minutes into parsing."
            ),
        ) from exc
    return path


@router.get("/{doc_id}/parse-status", response_model=ParseStatus)
async def parse_status(request: Request, doc_id: str) -> ParseStatus:
    """A failed parse reports `failed` with its reason. It never reports `complete` with an
    empty document (HR-3)."""
    svc = services(request)
    status = await maybe_await(svc.require("ingest").status(doc_id))
    if status is None:
        raise HTTPException(status_code=404, detail=f"no ingest job for document {doc_id!r}")
    return ParseStatus.model_validate(status)


@router.get("/{doc_id}/parse")
async def parse_view(request: Request, doc_id: str, version: int | None = None) -> dict:
    """Everything the parse inspector screen needs, in one call.

    Composed rather than merged: the IR is read from B1's version store, the reference
    tiers and orphan markers come from B1's ingest report, and the style from B1's
    detector. This route owns none of that data — it owns the shape F1 designed against
    (memory.md §5, F1 → B3).

    `counts` is the CP-2 conservation statement — resolved + parsed_unresolved +
    low_confidence + quarantined must equal `total_detected` — and it is returned whether
    or not it balances, because a parse that lost references needs to say so (HR-3).
    """
    svc = services(request)
    document = await load_document(svc, doc_id, version)
    report = await maybe_await(svc.require("ingest").parse_report(doc_id))

    style = None
    if svc.style is not None:
        style = await maybe_await(svc.style.detect(doc_id))

    return {
        "document": document.model_dump(mode="json"),
        "references": report.get("references", []),
        "orphan_markers": report.get("orphan_markers", []),
        "counts": report.get("counts", {}),
        "quarantine": [entry.model_dump(mode="json") for entry in document.quarantine],
        "style": style,
    }


@router.get("/{doc_id}", response_model=Document)
async def get_document(request: Request, doc_id: str, version: int | None = None) -> Document:
    return await load_document(services(request), doc_id, version)


@router.get("/{doc_id}/versions", response_model=VersionInfo)
async def list_versions(request: Request, doc_id: str) -> VersionInfo:
    svc = services(request)
    versions = await svc.require("documents").list_versions(doc_id)
    if not versions:
        raise HTTPException(status_code=404, detail=f"document {doc_id!r} does not exist")
    return VersionInfo(doc_id=doc_id, versions=versions, current=max(versions))


@router.post("/{doc_id}/revert")
async def revert(request: Request, doc_id: str, payload: RevertRequest) -> dict:
    """Reverting appends a new version whose content is the old one, so history stays
    append-only and a revert is itself revertible."""
    svc = services(request)
    result = await svc.versions().revert(doc_id, payload.to_version)
    return result.model_dump(mode="json")


@router.get("/{doc_id}/style", response_model=StyleResponse)
async def get_style(request: Request, doc_id: str) -> StyleResponse:
    """Round-trip style detection (ADR-011). `ambiguous` means the top two scored within
    0.05 and the user must choose — we do not guess and present it as a finding."""
    svc = services(request)
    result = await maybe_await(svc.require("style").detect(doc_id))
    if result is None:
        raise HTTPException(status_code=404, detail=f"no style result for document {doc_id!r}")
    return _style_response(doc_id, result)


@router.put("/{doc_id}/style", response_model=StyleResponse)
async def set_style(request: Request, doc_id: str, payload: StyleSelection) -> StyleResponse:
    svc = services(request)
    result = await maybe_await(svc.require("style").select(doc_id, payload.style_id))
    await _persist_style(svc, doc_id, payload.style_id)
    return _style_response(doc_id, result)


async def _persist_style(svc: Services, doc_id: str, style_id: str) -> None:
    """Write the user's choice onto the stored document, not only the ingest registry.

    B1's `select()` sets `metadata.style_id` on the in-process parse record. Nothing read
    that: the exporter loads the head from Postgres, where `style_id` stayed `None`, so a
    user who picked a style still got "no citation style selected" from the download — and
    the choice vanished entirely on the next API restart. The pick is a fact about the
    document, so it belongs on the document.

    Committed as a version rather than patched in place because the IR store is
    append-only and versions are the audit trail (CP-6): the style a paper was rendered
    in is exactly the kind of thing that should be revertible and visible in history.
    """
    document = await svc.require("documents").get(doc_id, None)
    if document is None:
        raise HTTPException(status_code=404, detail=f"document {doc_id!r} does not exist")
    if document.metadata.style_id == style_id:
        return

    document.metadata.style_id = style_id
    # A chosen style is the user's answer, not a measurement; a similarity score
    # reported next to it would misdescribe where it came from.
    document.metadata.style_ambiguous = False
    document.metadata.style_confidence = None
    await svc.require("documents").put_version(document, parent_version=document.version)


def _style_response(doc_id: str, result: dict) -> StyleResponse:
    """B1's style payload carries no `doc_id` — it was asked about one and answers about
    that one — while `StyleResponse` requires it, so validating the payload as-is raised a
    pydantic `ValidationError` and both style routes returned 500 on every successful
    detection. The only test of this route covered the 503 for an unbound style service,
    which is why the success path stayed broken.

    Supplied here rather than added to B1's return: the path parameter *is* the doc_id, and
    an adapter between another package's shape and F1's is this layer's job."""
    return StyleResponse.model_validate({"doc_id": doc_id, **result})


def _export_filename(doc_id: str, version: int) -> str:
    """One definition, because the manifest promises the name the download delivers."""
    return f"{doc_id}-v{version}.tex"


@router.get("/{doc_id}/export/manifest", response_model=ExportManifest)
async def export_manifest(
    request: Request, doc_id: str, version: int | None = None
) -> ExportManifest:
    """Describe the export before the user commits to it.

    Counted off the IR itself rather than by rendering: the manifest is what the export
    page loads on arrival, and rendering a whole paper through Pandoc to fill in three
    numbers would make the page slow and would fail for the very documents that most need
    the page to explain why (no style chosen yet).
    """
    svc = services(request)
    document = await load_document(svc, doc_id, version)

    placeholders: dict[str, int] = {"figure": 0, "table": 0, "equation": 0}
    source_ids: set[str] = set()
    for section in document.sections:
        for block in section.blocks:
            if block.type in placeholders:
                placeholders[block.type] += 1
            for span in block.spans:
                for anchor in span.citation_anchors:
                    source_ids.update(anchor.source_ids)

    style_id = document.metadata.style_id
    blocked_reason = None
    if not style_id:
        blocked_reason = (
            "No citation style has been chosen for this document, so the bibliography "
            "cannot be rendered. Style detection scored two candidates too close to call "
            "and will not guess — choose one on the parse screen, then export."
        )

    return ExportManifest(
        doc_id=doc_id,
        version=document.version,
        filename=_export_filename(doc_id, document.version),
        placeholder_blocks=[
            PlaceholderCount(type=kind, count=count)  # type: ignore[arg-type]
            for kind, count in placeholders.items()
        ],
        bibliography_entries=len(source_ids),
        style_id=style_id,
        exportable=blocked_reason is None,
        blocked_reason=blocked_reason,
    )


@router.get("/{doc_id}/export.tex", response_class=PlainTextResponse)
async def export_latex(request: Request, doc_id: str, version: int | None = None) -> PlainTextResponse:
    svc = services(request)
    document = await load_document(svc, doc_id, version)
    latex = await svc.require("exporter").to_latex(document)
    return PlainTextResponse(
        latex,
        media_type="application/x-tex",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_export_filename(doc_id, document.version)}"'
            )
        },
    )


__all__ = ["router"]
