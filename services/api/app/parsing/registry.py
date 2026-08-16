"""In-process record of what each ingest produced.

The API layer polls for progress, then asks for the parse report, then asks for a style —
three requests against one ingest. Something has to remember the ingest between them.

This is deliberately a process-local dictionary, not a table. The IR itself *is*
persisted and versioned (`app.ir.store`); what lives here is the ephemeral job state
around it — progress, stage, the reference list, the reconciliation notes. If the process
restarts mid-ingest the job is gone and `status()` returns `None`, which the API surfaces
as an unknown job rather than as a finished one. That is the honest failure and it is
better than a durable queue we would not have time to make correct.

Scaling past one API process means moving this to Redis. Nothing else has to change:
callers only touch `IngestRegistry`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # `pipeline` imports this module, so the type is a forward reference.
    from app.core.contracts import Document
    from app.parsing.pipeline import IngestResult

__all__ = ["JobState", "IngestRecord", "IngestRegistry", "STAGES"]

JobState = Literal["queued", "running", "complete", "failed"]

# Ordered, so progress is derived from position rather than invented per stage.
STAGES: tuple[str, ...] = (
    "queued",
    "grobid",
    "tei_to_ir",
    "references",
    "repair",
    "arbiter",
    "style",
    "persist",
    "complete",
)


@dataclass
class IngestRecord:
    doc_id: str
    job_id: str
    filename: str
    state: JobState = "queued"
    stage: str = "queued"
    version: int | None = None
    error: str = ""
    result: IngestResult | None = None
    draft_document: Document | None = None
    """The IR as it existed straight out of `tei_to_ir`: sections, blocks, spans, title —
    everything except a reconciled bibliography. Published minutes before the ingest
    finishes so a reader can be answered about the paper's *text* while its references are
    still being repaired and arbitrated (ADR-033).

    It is never a substitute for `result`. Anything served from here is marked
    `is_draft: true` all the way to the user, because the difference between "this is the
    text as extracted" and "this is the reconciled document" is exactly the kind of thing
    HR-3 says must be visible rather than inferred."""
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None

    @property
    def progress(self) -> float:
        """Fraction of the pipeline completed, from the stage's position in `STAGES`."""
        if self.state == "complete":
            return 1.0
        try:
            index = STAGES.index(self.stage)
        except ValueError:
            return 0.0
        return round(index / (len(STAGES) - 1), 3)

    def status(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "doc_id": self.doc_id,
            "state": self.state,
            "stage": self.stage,
            "progress": self.progress,
            "version": self.version,
            "error": self.error or None,
            "elapsed_s": round((self.finished_at or time.monotonic()) - self.started_at, 2),
        }


class IngestRegistry:
    def __init__(self) -> None:
        self._by_doc: dict[str, IngestRecord] = {}

    def create(self, doc_id: str, job_id: str, filename: str) -> IngestRecord:
        record = IngestRecord(doc_id=doc_id, job_id=job_id, filename=filename)
        self._by_doc[doc_id] = record
        return record

    def get(self, doc_id: str) -> IngestRecord | None:
        return self._by_doc.get(doc_id)

    def advance(self, doc_id: str, stage: str) -> None:
        record = self._by_doc.get(doc_id)
        if record is None:
            return
        record.state = "running"
        record.stage = stage

    def publish_draft(self, doc_id: str, document: Document) -> None:
        """Record the IR as soon as `tei_to_ir` has built it."""
        record = self._by_doc.get(doc_id)
        if record is None:
            return
        record.draft_document = document

    def draft(self, doc_id: str) -> Document | None:
        """The draft IR, or None when the ingest has not reached `tei_to_ir` yet.

        None means "nothing about this paper is readable except its filename", which is
        the honest answer at stages `queued` and `grobid`. Callers say exactly that rather
        than fabricating an intermediate.
        """
        record = self._by_doc.get(doc_id)
        return record.draft_document if record else None

    def complete(self, doc_id: str, result: IngestResult, version: int) -> None:
        record = self._by_doc.get(doc_id)
        if record is None:
            return
        record.state = "complete"
        record.stage = "complete"
        record.result = result
        record.version = version
        record.finished_at = time.monotonic()

    def fail(self, doc_id: str, message: str) -> None:
        """Record a failure. A failed ingest stays visible — it is never removed."""
        record = self._by_doc.get(doc_id)
        if record is None:
            record = self.create(doc_id, job_id=f"unknown:{doc_id}", filename="")
        record.state = "failed"
        record.error = message
        record.finished_at = time.monotonic()


# One registry per process, shared by the ingest pipeline and the style service.
_REGISTRY = IngestRegistry()


def registry() -> IngestRegistry:
    return _REGISTRY
