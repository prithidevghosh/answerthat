"""HTTP request and response shapes.

These are transport models, not domain models. Anything from Appendix A crosses the wire
as itself — `Document`, `ProposedChange`, `KernelVerdict`, `Finding` — because F1 is
building against Appendix A too, and a second parallel vocabulary between us is a bug
waiting to happen.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.agent.versioning import OrphanDecision


class JobAccepted(BaseModel):
    """Returned immediately by anything that runs in the background (ADR-014)."""

    job_id: str
    doc_id: str
    status: Literal["queued"] = "queued"
    poll: str
    stream: str | None = None


class ParseStatus(BaseModel):
    doc_id: str
    job_id: str | None = None
    state: Literal["queued", "running", "complete", "failed"]
    stage: str | None = None
    progress: float | None = None
    version: int | None = None
    error: str | None = None
    quarantined: int | None = None
    orphan_markers: int | None = None


class VersionInfo(BaseModel):
    doc_id: str
    versions: list[int]
    current: int


class RevertRequest(BaseModel):
    to_version: int


class StyleResponse(BaseModel):
    doc_id: str
    style_id: str | None
    score: float | None
    ambiguous: bool
    shortlist: list[dict] = Field(default_factory=list)
    note: str | None = None


class StyleSelection(BaseModel):
    style_id: str


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=4000)
    version: int | None = None  # None → the latest version


class ApprovalPayload(BaseModel):
    approved_change_ids: list[str] = Field(default_factory=list)
    rejected_change_ids: list[str] = Field(default_factory=list)
    orphan_decisions: list[OrphanDecision] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    section_ids: list[str] | None = None  # None → the whole paper (ADR-014's default)


__all__ = [
    "ApprovalPayload",
    "CommandRequest",
    "JobAccepted",
    "ParseStatus",
    "RevertRequest",
    "ReviewRequest",
    "StyleResponse",
    "StyleSelection",
    "VersionInfo",
]
