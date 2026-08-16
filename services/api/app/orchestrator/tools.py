"""The tool registry: schema, handler and policy for everything the agent can do.

One declarative registry, built per conversation and pinned to one `doc_id`. The agent's
competence lives here — if it cannot answer a question about a finding, the fix is a tool
that reads the finding, not a paragraph telling it to be helpful.

Three properties every tool in this file has, without exception.

**Both a summary and a payload, always.** `ToolResult.summary` is a short factual line the
model reads; `ToolResult.data` is the structured payload the frontend renders as a card.
Returning only one of them forces somebody to parse the other — the model reading JSON to
find a number, or the UI regex-ing prose to find a reference list — and both go wrong
silently. They are produced from the same facts in the same function, so they cannot
disagree.

**Failure is a value, not an exception.** `ok=False` with a reason the model can read,
relay and act on. A tool that raises has its exception turned into exactly this by the
runtime, so the loop never dies because a provider returned 429 (HR-3).

**Preconditions are checked by the tool, not by an ordering rule.** There is no fixed
sequence here: the researcher may ask to export before reviewing, ask a question in the
middle of a review, or ask for a second review of one section. Every tool validates its
own preconditions and says why when they are not met — `get_parse_report` refuses while
an ingest is running because a half-report reads as a paper with few references, and that
refusal is its business rather than the caller's.

Nothing in this module writes to `source_store`. There is no import that would make it
possible: `SourceReader` has `get`, `has` and `warm`, and HR-1 holds structurally.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.contracts import Document
from app.orchestrator.ports import (
    CommandGateway,
    DocumentReader,
    ExportGateway,
    IngestGateway,
    RetrievalIntrospector,
    ReviewGateway,
    SourceReader,
    StyleGateway,
    VersionGateway,
)

log = logging.getLogger("app.orchestrator.tools")

__all__ = ["Tool", "ToolContext", "ToolRegistry", "ToolResult", "Toolbox", "build_registry"]


# --------------------------------------------------------------------------- envelope


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def failed(cls, reason: str, data: dict[str, Any] | None = None) -> ToolResult:
        """A refusal, with the reason in both fields.

        In `summary` because that is what the model reads, and in `error` because that is
        what the UI renders as a broken tool line. The alternative — a summary saying
        "could not read the parse report" and an error saying why — makes the model relay
        the useless half.
        """
        return cls(ok=False, summary=reason, data=data or {}, error=reason)

    def as_event(self) -> dict[str, Any]:
        return {"ok": self.ok, "summary": self.summary, "data": self.data, "error": self.error}

    def for_model(self) -> str:
        """What goes into the `role="tool"` message.

        The summary first, then the payload. The model can answer most questions from the
        first line and still has the structured data when it needs an id.
        """
        import json  # noqa: PLC0415 — local, so the module imports cleanly without it

        body = json.dumps(self.data, ensure_ascii=False, default=str)
        head = self.summary if self.ok else f"FAILED: {self.error or self.summary}"
        return f"{head}\n{body}"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[..., Awaitable[ToolResult]]
    label: str
    """A short human phrase for the UI, e.g. "Reading the parse report". Chrome the UI
    owns; it is never presented as something the agent said."""
    mutating: bool = False
    """Writes a document version, or produces a file."""
    confirm: bool = False
    """Requires a user turn between the proposal and the execution. Enforced in the
    runtime (§4.3), not here and not in the prompt."""

    def as_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
                "strict": True,
            },
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        return [tool.as_openai() for tool in self._tools.values()]


# --------------------------------------------------------------------------- schemas


def _schema(**properties: dict) -> dict:
    """A strict JSON Schema object.

    OpenAI's strict mode requires `additionalProperties: false` and **every** property
    listed in `required`; optionality is expressed as a nullable type, not by omission.
    Building schemas through this helper rather than by hand is what keeps that true —
    a hand-written schema with an un-required property is accepted at definition time and
    rejected at call time, which is a long way from the mistake.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _str(description: str) -> dict:
    return {"type": "string", "description": description}


def _opt_str(description: str) -> dict:
    return {"type": ["string", "null"], "description": description}


def _opt_int(description: str) -> dict:
    return {"type": ["integer", "null"], "description": description}


def _enum(description: str, values: list[str], *, nullable: bool = False) -> dict:
    return {
        "type": ["string", "null"] if nullable else "string",
        "enum": [*values, None] if nullable else values,
        "description": description,
    }


_DOC_ID = _str("The document this conversation is about. Must match it exactly.")


# --------------------------------------------------------------------------- context


@dataclass
class ToolContext:
    """Everything the handlers reach for, bound once in `app/api/deps.py`.

    Every field is required. There is no half-equipped agent: a registry missing its
    exporter would present `export_latex` to the model and fail at the moment the user
    said yes, which is the worst possible time to discover a wiring problem. `deps.py`
    leaves the whole orchestrator unbound instead, and `require()` names what is missing.
    """

    ingest: IngestGateway
    documents: DocumentReader
    sources: SourceReader
    style: StyleGateway
    review: ReviewGateway
    retrieval: RetrievalIntrospector
    commands: CommandGateway
    versions: VersionGateway
    exporter: ExportGateway
    index: Any
    """`app.orchestrator.index.EvidenceIndex`. Typed loosely because it is ours, not a port."""
    settings: Any


# --------------------------------------------------------------------------- handlers


class Toolbox:
    """The handlers, bound to one document.

    `doc_id` is pinned at construction and every tool that takes one checks it. The model
    cannot reach a second document by passing a different id — there is one conversation
    per paper, and a tool call naming another paper is either a hallucinated id or a
    confusion, and both should fail loudly rather than answer about the wrong manuscript.
    """

    def __init__(self, ctx: ToolContext, doc_id: str) -> None:
        self.ctx = ctx
        self.doc_id = doc_id

    # -- helpers -----------------------------------------------------------

    def _check_doc(self, doc_id: str) -> ToolResult | None:
        if doc_id != self.doc_id:
            return ToolResult.failed(
                f"this conversation is about document {self.doc_id!r}, not {doc_id!r}. "
                "Use the document id given in your instructions."
            )
        return None

    def _page(self, limit: int | None, offset: int | None) -> tuple[int, int]:
        settings = self.ctx.settings
        size = limit if limit is not None else settings.orchestrator_page_size
        return (
            max(1, min(int(size), settings.orchestrator_page_size_max)),
            max(0, int(offset or 0)),
        )

    async def _status(self) -> dict[str, Any]:
        from app.api.adapters import maybe_await  # noqa: PLC0415 — interop shim, not a port

        return await maybe_await(self.ctx.ingest.status(self.doc_id)) or {}

    async def _resolve_document(
        self, version: int | None = None
    ) -> tuple[Document | None, bool, str]:
        """`(document, is_draft, reason_if_none)`. ADR-033.

        The persisted IR wins whenever it exists. The draft is served only while nothing
        has been persisted yet, and it is always flagged — "this is the text as extracted;
        the bibliography is still being reconciled" is a different claim from "this is the
        document", and a reader who cannot tell them apart will quote reference counts
        that are about to change.

        An explicit `version` never falls back to the draft: a caller asking for v2 wants
        that version, and answering with unversioned draft text would be a different
        document wearing the right number.
        """
        document = await self.ctx.documents.get(self.doc_id, version)
        if document is not None:
            return document, False, ""
        if version is not None:
            return None, False, f"document {self.doc_id!r} has no version {version}"

        draft = self.ctx.ingest.draft_document(self.doc_id)
        if draft is not None:
            return draft, True, ""

        status = await self._status()
        stage = status.get("stage", "unknown")
        state = status.get("state")
        if state == "failed":
            return None, False, f"parsing failed for this document: {status.get('error')}"
        if not status:
            return None, False, (
                f"nothing is known about document {self.doc_id!r} — no ingest for it exists in "
                "this process and no version of it is stored"
            )
        return None, False, (
            f"the paper's text is not readable yet: the ingest is at stage {stage!r}, before "
            "the PDF has been turned into a document. Only the filename is known at this "
            "point. Poll get_parse_progress."
        )

    @staticmethod
    def _outline(document: Document, *, is_draft: bool) -> dict[str, Any]:
        sections = []
        blocks = spans = anchors = 0
        for section in document.sections:
            section_spans = 0
            for block in section.blocks:
                blocks += 1
                for span in block.spans:
                    spans += 1
                    section_spans += 1
                    anchors += len(span.citation_anchors)
            sections.append(
                {
                    "section_id": section.id,
                    "title": section.title,
                    "level": section.level,
                    "order": section.order,
                    "blocks": len(section.blocks),
                    "spans": section_spans,
                }
            )
        return {
            "doc_id": document.doc_id,
            "version": document.version,
            "title": document.metadata.title,
            "style_id": document.metadata.style_id,
            "sections": sections,
            "counts": {
                "sections": len(document.sections),
                "blocks": blocks,
                "spans": spans,
                "citation_anchors": anchors,
                "quarantined": len(document.quarantine),
            },
            "is_draft": is_draft,
        }

    # -- parsing -----------------------------------------------------------

    async def get_parse_progress(self, doc_id: str) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        status = await self._status()
        if not status:
            return ToolResult.failed(
                f"no ingest is known for document {doc_id!r} in this process. If it was "
                "uploaded before the API last restarted, the in-flight job did not survive."
            )
        fraction = status.get("progress")
        data = {
            "state": status.get("state"),
            "stage": status.get("stage"),
            "fraction": fraction,
            "elapsed_s": status.get("elapsed_s"),
            "version": status.get("version"),
            "error": status.get("error"),
        }
        if status.get("state") == "failed":
            return ToolResult(
                ok=True,
                summary=f"Parsing failed at stage {status.get('stage')!r}: {status.get('error')}",
                data=data,
            )
        percent = f"{round((fraction or 0) * 100)}%" if fraction is not None else "unknown"
        return ToolResult(
            ok=True,
            summary=(
                f"Parsing is {status.get('state')} at stage {status.get('stage')!r} "
                f"({percent} of the pipeline), {status.get('elapsed_s')}s elapsed."
            ),
            data=data,
        )

    async def get_parse_report(self, doc_id: str, include: str | None = None) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        from app.api.adapters import maybe_await  # noqa: PLC0415
        from app.core.contracts import ParseFailure  # noqa: PLC0415

        try:
            report = await maybe_await(self.ctx.ingest.parse_report(doc_id))
        except ParseFailure as exc:
            # The refusal is the answer, and it is a *correct* one: a report served
            # mid-ingest would show a fraction of the references and read as a paper with
            # a short bibliography.
            return ToolResult.failed(str(exc))

        counts = report.get("counts", {})
        summary = (
            f"{counts.get('total_detected', 0)} reference(s) detected: "
            f"{counts.get('resolved', 0)} resolved, "
            f"{counts.get('parsed_unresolved', 0)} parsed but unresolved, "
            f"{counts.get('low_confidence', 0)} low confidence, "
            f"{counts.get('quarantined', 0)} quarantined. "
            f"{counts.get('orphan_marker', 0)} orphan marker(s)."
        )
        data: dict[str, Any] = {"counts": counts, "style_error": report.get("style_error")}
        if (include or "counts") == "full":
            data.update(
                {
                    "references": report.get("references", []),
                    "orphan_markers": report.get("orphan_markers", []),
                    "reconciliations": report.get("reconciliations", []),
                    "repairs": report.get("repairs", []),
                    "repair_skipped_reason": report.get("repair_skipped_reason"),
                }
            )
        return ToolResult(ok=True, summary=summary, data=data)

    async def get_document_outline(self, doc_id: str, version: int | None = None) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        document, is_draft, reason = await self._resolve_document(version)
        if document is None:
            return ToolResult.failed(reason)
        outline = self._outline(document, is_draft=is_draft)
        counts = outline["counts"]
        draft_note = (
            " This is the text as extracted; the bibliography is still being reconciled, so "
            "citation counts are not final."
            if is_draft
            else ""
        )
        return ToolResult(
            ok=True,
            summary=(
                f"{outline['title'] or 'Untitled'} — v{document.version}, "
                f"{counts['sections']} section(s), {counts['spans']} span(s), "
                f"{counts['citation_anchors']} citation anchor(s).{draft_note}"
            ),
            data=outline,
        )

    async def get_style(self, doc_id: str) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        from app.api.adapters import maybe_await  # noqa: PLC0415
        from app.core.errors import StyleDetectionFailure  # noqa: PLC0415

        try:
            result = await maybe_await(self.ctx.style.detect(doc_id))
        except StyleDetectionFailure as exc:
            # Re-scoring reads the in-process ingest record, which does not survive a
            # restart. The document carries the style that was recorded on it (ADR-030),
            # and that is what an export would actually render with — so it is the honest
            # answer, labelled as recorded rather than as freshly measured.
            document = await self.ctx.documents.get(doc_id)
            if document is None or not document.metadata.style_id:
                return ToolResult.failed(str(exc))
            return ToolResult(
                ok=True,
                summary=(
                    f"Style recorded on version {document.version}: "
                    f"{document.metadata.style_id}. This is the stored value, not a fresh "
                    f"measurement — {exc}"
                ),
                data={
                    "style_id": document.metadata.style_id,
                    "score": document.metadata.style_confidence,
                    "ambiguous": document.metadata.style_ambiguous,
                    "source": "recorded",
                    "detail": str(exc),
                },
            )
        if not result:
            return ToolResult.failed(f"no style result for document {doc_id!r}")
        ambiguous = result.get("ambiguous")
        return ToolResult(
            ok=True,
            summary=(
                f"Detected style: {result.get('style_id') or 'none'} "
                f"(round-trip score {result.get('score')})"
                + (
                    ". The top two candidates scored within the ambiguity margin, so this call "
                    "is close and the user may want to choose."
                    if ambiguous
                    else "."
                )
            ),
            data=dict(result),
        )

    async def set_style(self, doc_id: str, style_id: str) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        from app.api.adapters import maybe_await  # noqa: PLC0415
        from app.core.errors import StyleDetectionFailure  # noqa: PLC0415

        try:
            result = await maybe_await(self.ctx.style.select(doc_id, style_id))
        except StyleDetectionFailure as exc:
            return ToolResult.failed(str(exc))

        document = await self.ctx.documents.get(doc_id)
        if document is None:
            return ToolResult.failed(
                f"document {doc_id!r} is not stored yet, so the style choice has nowhere to be "
                "recorded. Wait for parsing to finish."
            )
        version = document.version
        if document.metadata.style_id != style_id:
            # Committed as a version rather than patched in place, exactly as
            # `_persist_style` does for the deterministic screen: the IR store is
            # append-only and the style a paper is rendered in is revertible history.
            document.metadata.style_id = style_id
            document.metadata.style_ambiguous = False
            document.metadata.style_confidence = None
            stored = await self.ctx.versions.set_style(document)
            version = stored
        return ToolResult(
            ok=True,
            summary=f"Citation style set to {style_id!r}, recorded on version {version}.",
            data={"style_id": style_id, "version": version, **dict(result or {})},
        )

    # -- review ------------------------------------------------------------

    async def describe_review_plan(self, doc_id: str) -> ToolResult:
        """What a review of *this* document will actually do, read from the live system.

        Every number here is introspected. An absent `SEMANTIC_SCHOLAR_API_KEY` shows up
        as one fewer retrieval strategy rather than as thinner results, because those look
        identical in the findings list and only one of them is our fault.
        """
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        settings = self.ctx.settings
        will_run, will_not_run = await self.ctx.retrieval.strategies_for(doc_id)

        document, is_draft, reason = await self._resolve_document()
        span_count = (
            sum(len(block.spans) for section in document.sections for block in section.blocks)
            if document is not None
            else None
        )

        # Deliberately not a claim-count estimate. The extractor is a model call over the
        # whole paper and its output is not predictable from span count — guessing "about
        # 40 claims" and delivering 12 would be a fabricated number in a tool whose entire
        # purpose is to replace fabricated numbers with introspected ones.
        data = {
            "strategies": will_run,
            "strategies_unavailable": will_not_run,
            "thresholds": {
                "rerank_keep": settings.rerank_keep,
                "verify_keep": settings.verify_keep,
                "citability_min": settings.citability_min,
            },
            "quote_check": (
                "every finding carries a verbatim quote that is mechanically substring-checked "
                "against the abstract actually fetched for that source; a quote that is not "
                "found discards the finding"
            ),
            "spans_in_document": span_count,
            "claims_estimate": None,
            "claims_estimate_note": (
                "not knowable cheaply — claim extraction is itself a model call over the paper, "
                "and its yield depends on how much of the text makes checkable assertions"
            ),
            "rate_limit_note": (
                "providers are called at roughly 1 request per second and claims are processed "
                "in citability order, so a full paper typically takes five to eight minutes; "
                "findings stream as each one verifies rather than arriving at the end"
            ),
            "document_is_draft": is_draft,
            "document_unavailable_reason": reason or None,
        }
        return ToolResult(
            ok=True,
            summary=(
                f"{len(will_run)} of 4 retrieval strategies will run for this document "
                f"({', '.join(will_run) or 'none'})"
                + (f"; unavailable: {', '.join(will_not_run)}" if will_not_run else "")
                + f". Reranker keeps {settings.rerank_keep}, verifier sees "
                f"{settings.verify_keep}, claims below citability {settings.citability_min} are "
                "skipped. Expect five to eight minutes at ~1 request/second."
            ),
            data=data,
        )

    async def start_review(
        self, doc_id: str, section_ids: list[str] | None = None, force: bool | None = None
    ) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        document = await self.ctx.documents.get(doc_id)
        if document is None:
            return ToolResult.failed(
                "this document is not persisted yet, so there is nothing to review. A review "
                "reads the reconciled bibliography, which does not exist until parsing "
                "finishes. Poll get_parse_progress."
            )
        from app.api.adapters import maybe_await  # noqa: PLC0415

        job_id = await maybe_await(
            self.ctx.review.start(doc_id, section_ids or None, force=bool(force))
        )
        return ToolResult(
            ok=True,
            summary=(
                f"Review job {job_id} is running over "
                + (f"{len(section_ids)} section(s)" if section_ids else "the whole paper")
                + ". Findings stream as they verify; ask for progress rather than waiting."
            ),
            data={"job_id": job_id, "doc_id": doc_id, "section_ids": section_ids or None},
        )

    async def get_review_progress(self, doc_id: str) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        from app.api.adapters import maybe_await  # noqa: PLC0415

        status = await maybe_await(self.ctx.review.status(doc_id)) or {}
        state = status.get("status", "not_started")
        if state == "not_started":
            return ToolResult(
                ok=True,
                summary="No review has been started for this document yet.",
                data={"status": "not_started"},
            )
        # Every counter, not a selection of them. "4 findings" and "4 findings, 11
        # candidates killed on the quote check, 6 abstracts unavailable" are different
        # reports of the same run, and the second one is the honest one.
        return ToolResult(
            ok=True,
            summary=(
                f"Review is {state}: {status.get('verified', 0)} of {status.get('total', 0)} "
                f"claim(s) verified, {status.get('findings_emitted', 0)} finding(s) emitted. "
                f"{status.get('candidates_considered', 0)} candidate(s) reached the verifier; "
                f"{status.get('quote_check_failures', 0)} discarded on the quote check; "
                f"{status.get('unverifiable_no_abstract', 0)} had no retrievable abstract; "
                f"{status.get('claims_without_candidates', 0)} claim(s) found no candidates."
                + (f" Error: {status.get('error')}" if status.get("error") else "")
            ),
            data=dict(status),
        )

    async def list_findings(
        self,
        doc_id: str,
        kind: str | None = None,
        severity: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        findings = self.ctx.review.findings(doc_id)
        matching = [
            finding
            for finding in findings
            if (kind is None or finding.get("kind") == kind)
            and (severity is None or finding.get("severity") == severity)
        ]
        size, start = self._page(limit, offset)
        page = matching[start : start + size]

        from app.api.adapters import maybe_await  # noqa: PLC0415

        status = await maybe_await(self.ctx.review.status(doc_id)) or {}
        running = status.get("status") == "running"
        return ToolResult(
            ok=True,
            summary=(
                f"{len(matching)} finding(s) match"
                + (f" (kind={kind})" if kind else "")
                + (f" (severity={severity})" if severity else "")
                + f"; showing {len(page)} from offset {start}."
                + (
                    " The review is still running, so this list is not final."
                    if running
                    else ""
                )
            ),
            data={
                "findings": [_finding_summary(finding) for finding in page],
                "total_matching": len(matching),
                "offset": start,
                "limit": size,
                "review_status": status.get("status", "not_started"),
                "review_complete": status.get("status") == "complete",
            },
        )

    async def get_finding(self, doc_id: str, finding_id: str) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        for finding in self.ctx.review.findings(doc_id):
            if finding.get("finding_id") != finding_id:
                continue
            source_id = finding.get("source_id")
            record = None
            if source_id:
                await self.ctx.sources.warm([source_id])
                stored = self.ctx.sources.get(source_id)
                record = stored.model_dump(mode="json") if stored is not None else None
            verification = finding.get("verification") or {}
            return ToolResult(
                ok=True,
                summary=(
                    f"{finding.get('kind')} ({finding.get('severity')}): "
                    f"claim {finding.get('claim', {}).get('claim_id')} — "
                    f"{verification.get('label') or 'no verification'}"
                ),
                data={
                    **finding,
                    "source_record": record,
                    "external_url": finding.get("external_url")
                    or (record or {}).get("provenance", {}).get("external_url"),
                },
            )
        return ToolResult.failed(
            f"no finding {finding_id!r} in this document's review. Finding ids come from "
            "list_findings; do not construct one."
        )

    async def list_claims(
        self, doc_id: str, limit: int | None = None, offset: int | None = None
    ) -> ToolResult:
        """The claims this review extracted, read back off its findings.

        Not re-extracted. Claim extraction is a model call over the whole paper, and
        running it again to answer "what did you check?" would bill a second pass and
        could return a *different* set from the one the findings were computed against —
        which is worse than not answering.
        """
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        seen: dict[str, dict] = {}
        for finding in self.ctx.review.findings(doc_id):
            claim = finding.get("claim") or {}
            claim_id = claim.get("claim_id")
            if claim_id and claim_id not in seen:
                seen[claim_id] = claim
        claims = sorted(seen.values(), key=lambda c: c.get("citability", 0.0), reverse=True)
        size, start = self._page(limit, offset)
        page = claims[start : start + size]
        if not claims:
            return ToolResult(
                ok=True,
                summary=(
                    "No claims are available. They are produced by a review — either none has "
                    "run for this document, or it has not reached its first claim yet."
                ),
                data={"claims": [], "total": 0},
            )
        return ToolResult(
            ok=True,
            summary=f"{len(claims)} claim(s) checked so far; showing {len(page)} from {start}.",
            data={"claims": page, "total": len(claims), "offset": start, "limit": size},
        )

    # -- sources and question answering ------------------------------------

    async def get_source(self, source_id: str) -> ToolResult:
        """One record from the append-only store. Read-only. HR-1.

        An id that is not in the store comes back `ok=False`. That is the case this tool
        exists to make impossible to get wrong: the store is the only thing that can
        confirm a source is real, and an id the model produced from memory rather than
        from a tool result will land here and be refused rather than described.
        """
        if not source_id.strip():
            return ToolResult.failed("no source_id was given")
        await self.ctx.sources.warm([source_id])
        record = self.ctx.sources.get(source_id)
        if record is None:
            return ToolResult.failed(
                f"there is no source {source_id!r} in the store. Every source_id in this system "
                "exists because a provider adapter saw it in an HTTP response (HR-1) — an id "
                "that is not here was not retrieved, so there is nothing to describe. Use an id "
                "from a finding or from the parse report."
            )
        payload = record.model_dump(mode="json")
        csl = payload.get("csl", {}) or {}
        return ToolResult(
            ok=True,
            summary=(
                f"{csl.get('title') or '(untitled)'} — "
                f"{payload.get('provenance', {}).get('provider')}, "
                f"abstract: {payload.get('abstract_source')}"
            ),
            data=payload,
        )

    async def search_evidence(
        self,
        doc_id: str,
        query: str,
        k: int | None = None,
        kinds: list[str] | None = None,
    ) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        settings = self.ctx.settings
        wanted = min(
            int(k if k is not None else settings.orchestrator_search_k),
            settings.orchestrator_search_k_max,
        )
        hits, status = await self.ctx.index.search(doc_id, query, k=wanted, kinds=kinds or None)

        building = status.get("state") == "building"
        missing = status.get("kinds_missing") or []
        caveat = ""
        if building:
            caveat = " The index is still building, so these results are partial."
        elif missing:
            caveat = (
                f" Nothing of kind {', '.join(missing)} is indexed yet, so this searched only "
                f"{', '.join(status.get('kinds_indexed') or ['nothing'])}."
            )
        return ToolResult(
            ok=True,
            summary=f"{len(hits)} result(s) for {query!r}.{caveat}",
            data={
                "hits": [
                    {"kind": hit.kind, "ref_id": hit.ref_id, "text": hit.text, "score": round(hit.score, 4)}
                    for hit in hits
                ],
                "index": status,
            },
        )

    async def read_section(self, doc_id: str, section_id: str) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        document, is_draft, reason = await self._resolve_document()
        if document is None:
            return ToolResult.failed(reason)
        for section in document.sections:
            if section.id != section_id:
                continue
            blocks: list[dict[str, Any]] = [
                {
                    "block_id": block.id,
                    "type": block.type,
                    "order": block.order,
                    "placeholder_caption": block.placeholder_caption,
                    "spans": [
                        {
                            "span_id": span.id,
                            "text": span.text,
                            "anchors": [
                                {
                                    "anchor_id": anchor.anchor_id,
                                    "marker": anchor.original_marker_text,
                                    "source_ids": list(anchor.source_ids),
                                }
                                for anchor in span.citation_anchors
                            ],
                        }
                        for span in block.spans
                    ],
                }
                for block in section.blocks
            ]
            return ToolResult(
                ok=True,
                summary=(
                    f"Section {section.title!r}: {len(blocks)} block(s), "
                    f"{sum(len(b['spans']) for b in blocks)} span(s)."
                    + (" Draft text — references not yet reconciled." if is_draft else "")
                ),
                data={
                    "section_id": section.id,
                    "title": section.title,
                    "level": section.level,
                    "blocks": blocks,
                    "is_draft": is_draft,
                },
            )
        return ToolResult.failed(
            f"no section {section_id!r} in this document. Section ids come from "
            "get_document_outline."
        )

    async def get_span(self, doc_id: str, span_id: str) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        document, is_draft, reason = await self._resolve_document()
        if document is None:
            return ToolResult.failed(reason)
        for section in document.sections:
            for block in section.blocks:
                for span in block.spans:
                    if span.id != span_id:
                        continue
                    return ToolResult(
                        ok=True,
                        summary=span.text[:200]
                        + (" Draft text — references not yet reconciled." if is_draft else ""),
                        data={
                            "span_id": span.id,
                            "text": span.text,
                            "block_id": block.id,
                            "section_id": section.id,
                            "section_title": section.title,
                            "anchors": [
                                {
                                    "anchor_id": anchor.anchor_id,
                                    "marker": anchor.original_marker_text,
                                    "source_ids": list(anchor.source_ids),
                                    "offset_in_span": anchor.offset_in_span,
                                }
                                for anchor in span.citation_anchors
                            ],
                            "is_draft": is_draft,
                        },
                    )
        return ToolResult.failed(
            f"no span {span_id!r} in this document. Span ids come from read_section or "
            "search_evidence."
        )

    # -- editing -----------------------------------------------------------

    async def propose_edit(self, doc_id: str, instruction: str) -> ToolResult:
        """Plan → execute → kernel → propose. Writes nothing.

        A change set with `status="failed"` is a real answer rather than an error: the
        planner could not produce something the kernel would accept, and the reasons are
        the kernel's own words. They are passed through unaltered — softening a rejection
        is how a refusal becomes a success story.
        """
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        if not instruction.strip():
            return ToolResult.failed("no instruction was given")
        document = await self.ctx.documents.get(doc_id)
        if document is None:
            return ToolResult.failed(
                "this document is not persisted yet, so there is nothing to edit. Wait for "
                "parsing to finish."
            )

        change_set = await self.ctx.commands.propose(document, instruction)
        changes = change_set.get("changes", [])
        orphans = [orphan for change in changes for orphan in change.get("orphans", [])]
        rejected = change_set.get("rejected", [])

        if change_set.get("status") == "failed":
            return ToolResult(
                ok=True,
                summary=(
                    "The edit could not be planned into anything the invariant kernel accepts. "
                    "Nothing was written. The kernel's reasons: "
                    + "; ".join(
                        reason for item in rejected for reason in item.get("reasons", [])
                    )
                ),
                data=change_set,
            )
        return ToolResult(
            ok=True,
            summary=(
                f"Proposed {len(changes)} change(s) against version "
                f"{change_set.get('base_version')} as change set "
                f"{change_set.get('change_set_id')}. Nothing is written yet."
                + (
                    f" {len(orphans)} citation anchor(s) could not be reattached and need a "
                    "keep/move/remove decision each before this can be committed."
                    if orphans
                    else ""
                )
                + (f" {len(rejected)} operation(s) were rejected." if rejected else "")
            ),
            data=change_set,
        )

    async def commit_change_set(
        self,
        change_set_id: str,
        approved_change_ids: list[str],
        rejected_change_ids: list[str] | None = None,
        orphan_decisions: list[dict] | None = None,
    ) -> ToolResult:
        """Write the approved subset as a new document version.

        `confirm=True`, so the runtime will not let this run in the turn that proposed the
        change set. What this handler adds is the two refusals that a confirmation cannot
        settle: a moved head (ADR-021), and an orphaned anchor with no decision (HR-5).
        Both come back as `ok=False` with the reason, for the agent to explain and re-plan.
        """
        from app.agent.store import ChangeSetNotFound  # noqa: PLC0415 — error type only
        from app.agent.versioning import ApprovalError, VersionConflict  # noqa: PLC0415
        from app.core.contracts import KernelRejection  # noqa: PLC0415

        try:
            change_set = self.ctx.commands.get_change_set(change_set_id)
        except ChangeSetNotFound as exc:
            return ToolResult.failed(str(exc.args[0] if exc.args else exc))

        if change_set.get("doc_id") != self.doc_id:
            return ToolResult.failed(
                f"change set {change_set_id!r} belongs to document {change_set.get('doc_id')!r}, "
                f"not to this conversation's {self.doc_id!r}."
            )
        if change_set.get("status") == "failed":
            return ToolResult.failed(
                "this change set produced no valid changes, so there is nothing to commit. Its "
                "`rejected` entries carry the kernel's reasons."
            )

        try:
            result = await self.ctx.versions.commit(
                change_set_id,
                # Pinned from the stored proposal, never from the model's arguments. The
                # user confirmed the version they were shown; letting the model restate it
                # would make ADR-021's optimistic lock a formality it could talk its way
                # around.
                base_version=int(change_set.get("base_version", 0)),
                approved_change_ids=list(approved_change_ids or []),
                rejected_change_ids=list(rejected_change_ids or []),
                orphan_decisions=list(orphan_decisions or []),
            )
        except VersionConflict as exc:
            return ToolResult.failed(
                f"{exc.detail} Nothing was written.",
                {
                    "kind": "version_conflict",
                    "doc_id": exc.doc_id,
                    "base_version": exc.base_version,
                    "current_version": exc.current_version,
                },
            )
        except ApprovalError as exc:
            return ToolResult.failed(str(exc), {"kind": "approval_invalid"})
        except KernelRejection as exc:
            return ToolResult.failed(str(exc), {"kind": "kernel_rejection"})

        if not result.get("committed"):
            return ToolResult(ok=True, summary=result.get("message", "Nothing was written."), data=result)
        return ToolResult(
            ok=True,
            summary=result.get("message", f"Committed version {result.get('new_version')}."),
            data=result,
        )

    async def revert_document(self, doc_id: str, to_version: int) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        from app.agent.versioning import ApprovalError  # noqa: PLC0415

        try:
            result = await self.ctx.versions.revert(doc_id, int(to_version))
        except ApprovalError as exc:
            return ToolResult.failed(str(exc))
        return ToolResult(
            ok=True, summary=result.get("message", "Reverted."), data=result
        )

    # -- export ------------------------------------------------------------

    async def get_export_manifest(self, doc_id: str, version: int | None = None) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        from app.core.errors import ExportFailure  # noqa: PLC0415

        try:
            manifest = await self.ctx.exporter.manifest(doc_id, version)
        except ExportFailure as exc:
            return ToolResult.failed(str(exc))
        except KeyError as exc:
            return ToolResult.failed(str(exc))

        placeholders = manifest.get("placeholder_blocks", [])
        total = sum(item.get("count", 0) for item in placeholders)
        disclosure = (
            f"{total} block(s) — "
            + ", ".join(f"{item['count']} {item['type']}" for item in placeholders if item.get("count"))
            + " — export as visible placeholders carrying their captions, not as the original "
            "figure, table or equation (ADR-008). The exported .tex is not a drop-in "
            "replacement for the original manuscript."
        ) if total else "No figures, tables or equations, so nothing exports as a placeholder."
        return ToolResult(
            ok=True,
            summary=(
                f"Version {manifest.get('version')}, style {manifest.get('style_id') or 'none'}, "
                f"{manifest.get('bibliography_entries')} bibliography entr(ies). "
                + disclosure
                + (
                    f" Not exportable: {manifest.get('blocked_reason')}"
                    if not manifest.get("exportable")
                    else ""
                )
            ),
            data={**manifest, "placeholder_disclosure": disclosure},
        )

    async def export_latex(self, doc_id: str, version: int | None = None) -> ToolResult:
        if (bad := self._check_doc(doc_id)) is not None:
            return bad
        from app.core.errors import ExportFailure  # noqa: PLC0415

        try:
            payload = await self.ctx.exporter.to_latex(doc_id, version)
        except ExportFailure as exc:
            # The exporter refuses on conditions it understands precisely — no style
            # chosen, a cited source with no CSL record — and says why. That message is
            # the answer; replacing it with "export failed" would turn a stated refusal
            # into "something broke".
            return ToolResult.failed(str(exc))
        return ToolResult(
            ok=True,
            summary=(
                f"Rendered {payload.get('filename')} ({payload.get('byte_size')} bytes) in style "
                f"{payload.get('style_id')}."
                + (
                    " The style was an ambiguous detection, so citations may not match the "
                    "target venue exactly."
                    if payload.get("style_uncertain")
                    else ""
                )
            ),
            data=payload,
        )


def _finding_summary(finding: dict) -> dict:
    """A finding trimmed to what a list needs.

    Full findings carry the whole claim, the CSL record and the matched passage; twenty of
    them would crowd out the conversation they are supposed to inform. The id is here, and
    `get_finding` returns everything.
    """
    claim = finding.get("claim") or {}
    verification = finding.get("verification") or {}
    return {
        "finding_id": finding.get("finding_id"),
        "kind": finding.get("kind"),
        "severity": finding.get("severity"),
        "source_id": finding.get("source_id"),
        "claim_id": claim.get("claim_id"),
        "claim_text": claim.get("text"),
        "span_id": claim.get("span_id"),
        "citability": claim.get("citability"),
        "verification_label": verification.get("label"),
        "external_url": finding.get("external_url"),
    }


# --------------------------------------------------------------------------- registry


def build_registry(ctx: ToolContext, doc_id: str) -> ToolRegistry:
    """The tools available in one conversation, bound to one document.

    Descriptions say what a tool does **and when not to use it**. The second half is what
    stops the agent calling `get_parse_report` every turn while an ingest runs, or
    reaching for `search_evidence` when it already has the id it needs.
    """
    box = Toolbox(ctx, doc_id)
    return ToolRegistry(
        [
            # -- parsing ---------------------------------------------------
            Tool(
                name="get_parse_progress",
                description=(
                    "Current state of the PDF ingest: stage, real completion fraction, elapsed "
                    "seconds, and the error if it failed. Use it to answer 'how far along is "
                    "it?' and to decide whether the parse report is available yet. Do not poll "
                    "it repeatedly in one turn — the user sees a live progress bar already."
                ),
                schema=_schema(doc_id=_DOC_ID),
                handler=box.get_parse_progress,
                label="Checking parse progress",
            ),
            Tool(
                name="get_parse_report",
                description=(
                    "The reference tier counts (resolved / parsed but unresolved / low "
                    "confidence / quarantined / orphan markers), and with include='full' the "
                    "full reference list, reconciliation notes, repair outcomes and orphan "
                    "markers. Refuses while the ingest is still running. Use 'counts' unless "
                    "the user asked to see the references themselves — 'full' is long."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    include=_enum(
                        "'counts' for the tier counts only, 'full' for every reference and "
                        "orphan marker. Defaults to 'counts'.",
                        ["counts", "full"],
                        nullable=True,
                    ),
                ),
                handler=box.get_parse_report,
                label="Reading the parse report",
            ),
            Tool(
                name="get_document_outline",
                description=(
                    "Title, version, section list with ids, and block/span/anchor counts. "
                    "`is_draft: true` means the ingest has produced the text but not yet "
                    "reconciled the bibliography — say so when reporting anything from it. "
                    "This is where section ids come from; use it before read_section."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    version=_opt_int("A specific version, or null for the current one."),
                ),
                handler=box.get_document_outline,
                label="Reading the document outline",
            ),
            Tool(
                name="get_style",
                description=(
                    "The detected citation style with its round-trip similarity score, and "
                    "whether the top two candidates were close enough to be ambiguous. Use "
                    "before an export, and whenever the user asks what style the paper is in."
                ),
                schema=_schema(doc_id=_DOC_ID),
                handler=box.get_style,
                label="Reading the citation style",
            ),
            Tool(
                name="set_style",
                description=(
                    "Record the user's chosen citation style. Commits a new document version, "
                    "so only call it when the user has actually named a style — not to resolve "
                    "an ambiguous detection on their behalf."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    style_id=_str("A style id from the shortlist returned by get_style."),
                ),
                handler=box.set_style,
                label="Setting the citation style",
                mutating=True,
            ),
            # -- review ----------------------------------------------------
            Tool(
                name="describe_review_plan",
                description=(
                    "What a review of this document would actually do, introspected from the "
                    "live system: which retrieval strategies can run, the current thresholds, "
                    "how findings are quote-checked, and the expected duration. Call this "
                    "before start_review and tell the user in your own words. Do not describe "
                    "a review from memory — the answer differs per document and per deployment."
                ),
                schema=_schema(doc_id=_DOC_ID),
                handler=box.describe_review_plan,
                label="Working out the review plan",
            ),
            Tool(
                name="start_review",
                description=(
                    "Start the review. Idempotent: a running or completed review of the same "
                    "scope returns the existing job rather than billing a second pass. Call it "
                    "only after the user has agreed to the plan you described."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    section_ids={
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                        "description": "Limit the review to these sections, or null for the whole paper.",
                    },
                    force={
                        "type": ["boolean", "null"],
                        "description": "Re-run a completed review of the same scope. Costs a full second pass.",
                    },
                ),
                handler=box.start_review,
                label="Starting the review",
            ),
            Tool(
                name="get_review_progress",
                description=(
                    "Every review counter: claims verified out of total, findings emitted, "
                    "candidates considered, quote-check failures, abstracts unavailable, and "
                    "claims that found no candidates. Use the secondary counters when a short "
                    "findings list needs explaining."
                ),
                schema=_schema(doc_id=_DOC_ID),
                handler=box.get_review_progress,
                label="Checking review progress",
            ),
            Tool(
                name="list_findings",
                description=(
                    "The review's findings, newest search first, filterable by kind and "
                    "severity and paged. Returns summaries with ids; call get_finding for the "
                    "quote, the source record and the URL."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    kind=_enum(
                        "Filter by finding kind, or null for all.",
                        ["missing_work", "claim_citation_mismatch", "no_candidates_found"],
                        nullable=True,
                    ),
                    severity=_enum(
                        "Filter by severity, or null for all.",
                        ["high", "medium", "low", "info"],
                        nullable=True,
                    ),
                    limit=_opt_int("How many to return; null for the default."),
                    offset=_opt_int("Where to start; null for 0."),
                ),
                handler=box.list_findings,
                label="Listing findings",
            ),
            Tool(
                name="get_finding",
                description=(
                    "One finding in full: the claim it is about, the verification label, the "
                    "verbatim quote that was substring-checked against the abstract, the source "
                    "record and its external URL. Use this before describing any finding."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    finding_id=_str("An id from list_findings. Never construct one."),
                ),
                handler=box.get_finding,
                label="Reading a finding",
            ),
            # -- sources and question answering ----------------------------
            Tool(
                name="get_source",
                description=(
                    "One source record from the append-only store: CSL-JSON, abstract, "
                    "provenance and external URL. Read-only. An id that is not in the store is "
                    "refused — every source_id exists because a provider returned it, so use "
                    "ids from findings or the parse report and never invent one."
                ),
                schema=_schema(source_id=_str("A source_id from a tool result.")),
                handler=box.get_source,
                label="Fetching a source record",
            ),
            Tool(
                name="search_evidence",
                description=(
                    "Semantic search over this paper's spans and its review's abstracts, claims "
                    "and findings. Use it to find *which* part of the paper or which reference "
                    "a question is about, then follow up with the exact tool for that kind. It "
                    "is a router into structured data, not a substitute for it: never quote a "
                    "hit without reading it through read_section, get_span or get_source."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    query=_str("What to look for, in natural language."),
                    k=_opt_int("How many hits; null for the default."),
                    kinds={
                        "type": ["array", "null"],
                        "items": {"type": "string", "enum": ["span", "abstract", "claim", "finding"]},
                        "description": "Restrict to these kinds, or null for all of them.",
                    },
                ),
                handler=box.search_evidence,
                label="Searching the paper",
            ),
            Tool(
                name="read_section",
                description=(
                    "Verbatim text of one section, span by span, with each span's citation "
                    "anchors. Use this to quote the paper rather than paraphrasing from memory."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    section_id=_str("A section id from get_document_outline."),
                ),
                handler=box.read_section,
                label="Reading a section",
            ),
            Tool(
                name="get_span",
                description=(
                    "One span's verbatim text with its anchors and its place in the document. "
                    "Use it when a finding or a search hit named a span_id."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    span_id=_str("A span id from read_section, a finding, or search_evidence."),
                ),
                handler=box.get_span,
                label="Reading a sentence",
            ),
            Tool(
                name="list_claims",
                description=(
                    "The atomic claims this review extracted, with their citability scores, "
                    "highest first. Available only once a review has produced findings."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    limit=_opt_int("How many to return; null for the default."),
                    offset=_opt_int("Where to start; null for 0."),
                ),
                handler=box.list_claims,
                label="Listing claims",
            ),
            # -- editing ---------------------------------------------------
            Tool(
                name="propose_edit",
                description=(
                    "Turn a natural-language editing instruction into a checked proposal: the "
                    "typed operations, the kernel's verdict on each, the structural diff, the "
                    "citation ledger, and any citation anchors that could not be reattached. "
                    "Writes nothing. Always show the user what came back — especially the "
                    "rejections, in the kernel's own words — before committing anything."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    instruction=_str("The edit, in the user's own words where possible."),
                ),
                handler=box.propose_edit,
                label="Planning the edit",
            ),
            Tool(
                name="commit_change_set",
                description=(
                    "Write the approved changes as a new document version. Requires that the "
                    "user has answered since you showed them the proposal. Every orphaned "
                    "citation anchor in the approved changes needs its own decision — keep, "
                    "move (with a target span) or remove — collected from the user, never "
                    "chosen for them. The commit is refused while any anchor is undecided."
                ),
                schema=_schema(
                    change_set_id=_str("The id returned by propose_edit."),
                    approved_change_ids={
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The change ids the user approved.",
                    },
                    rejected_change_ids={
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                        "description": "The change ids the user rejected, or null.",
                    },
                    orphan_decisions={
                        "type": ["array", "null"],
                        "items": {
                            "type": "object",
                            "properties": {
                                "anchor_id": {"type": "string"},
                                "action": {"type": "string", "enum": ["keep", "move", "remove"]},
                                "target_span_id": {"type": ["string", "null"]},
                            },
                            "required": ["anchor_id", "action", "target_span_id"],
                            "additionalProperties": False,
                        },
                        "description": (
                            "One decision per orphaned anchor, as stated by the user. "
                            "target_span_id is required for 'move' and null otherwise."
                        ),
                    },
                ),
                handler=box.commit_change_set,
                label="Committing the change",
                mutating=True,
                confirm=True,
            ),
            Tool(
                name="revert_document",
                description=(
                    "Restore the content of an earlier version by writing it as a new version. "
                    "History stays append-only, so a revert is itself revertible. Requires the "
                    "user to have confirmed the version number since you named it."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    to_version={"type": "integer", "description": "The version to restore."},
                ),
                handler=box.revert_document,
                label="Reverting the document",
                mutating=True,
                confirm=True,
            ),
            # -- export ----------------------------------------------------
            Tool(
                name="get_export_manifest",
                description=(
                    "What the export will contain before it runs: placeholder counts for "
                    "figures, tables and equations, bibliography size, the style, and whether "
                    "the export is possible at all. State the placeholder disclosure to the "
                    "user before they download anything."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    version=_opt_int("A specific version, or null for the current one."),
                ),
                handler=box.get_export_manifest,
                label="Reading the export manifest",
            ),
            Tool(
                name="export_latex",
                description=(
                    "Render the document to LaTeX and hand the user a file. Requires the user "
                    "to have confirmed since you disclosed the manifest. If the exporter "
                    "refuses, relay its reason exactly — it refuses on conditions it "
                    "understands, such as no citation style having been chosen."
                ),
                schema=_schema(
                    doc_id=_DOC_ID,
                    version=_opt_int("A specific version, or null for the current one."),
                ),
                handler=box.export_latex,
                label="Rendering the LaTeX export",
                mutating=True,
                confirm=True,
            ),
        ]
    )
