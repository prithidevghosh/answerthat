"""HTTP request and response shapes.

These are transport models, not domain models. Anything from Appendix A crosses the wire
as itself — `Document`, `ProposedChange`, `KernelVerdict`, `Finding` — because F1 is
building against Appendix A too, and a second parallel vocabulary between us is a bug
waiting to happen.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

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
    """ADR-021: a command names the version it was composed against.

    `version` is accepted as an alias so the field can be renamed without breaking a client
    mid-flight; `base_version` is the name to use.
    """

    model_config = ConfigDict(populate_by_name=True)

    command: str = Field(min_length=1, max_length=4000)
    base_version: int | None = Field(
        default=None,
        validation_alias=AliasChoices("base_version", "version"),
        description="the version this command was composed against; omit for the current head",
    )


class ApprovalPayload(BaseModel):
    """ADR-021: an approval names the version it was composed against, and it is required.

    Not optional, unlike a command's: by the time the user is approving, they have read a
    specific diff against a specific version. Letting that approval land on whatever the
    head happens to be is the silent lost update HR-3 exists to prevent.
    """

    base_version: int
    approved_change_ids: list[str] = Field(default_factory=list)
    rejected_change_ids: list[str] = Field(default_factory=list)
    orphan_decisions: list[OrphanDecision] = Field(default_factory=list)


class VersionConflictDetail(BaseModel):
    """The 409 body for a moved head. Carries `current_version` so the UI can re-plan
    against it rather than asking the user to work out what happened."""

    error: Literal["version_conflict"] = "version_conflict"
    doc_id: str
    base_version: int
    current_version: int | None
    detail: str
    hint: str = (
        "Nothing was written. Re-read the document at current_version and re-issue the "
        "command; the intervening version is someone's accepted edit and merging IR "
        "fragments automatically is not something we can do safely (ADR-021)."
    )


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
    "VersionConflictDetail",
    "VersionInfo",
]
