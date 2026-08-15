"""The edit console: submit a command, inspect the proposal, approve it.

Two requests, always, and never one. `POST /commands` produces a proposal and writes
nothing; `POST /approve` is the only thing in this service that creates a document version.
Collapsing them into a single "just do it" call would make the approval gate a formality,
and the approval gate is the product.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.agent.loop import ProposedChangeSet
from app.agent.metrics import METRICS, OperationMetrics
from app.agent.versioning import ApprovalRequest, CommitResult
from app.api.deps import Services
from app.api.routes.documents import load_document
from app.api.schemas import ApprovalPayload, CommandRequest

router = APIRouter(prefix="/api", tags=["edits"])


def services(request: Request) -> Services:
    return request.app.state.services


@router.post("/documents/{doc_id}/commands", response_model=ProposedChangeSet)
async def submit_command(request: Request, doc_id: str, payload: CommandRequest) -> ProposedChangeSet:
    """Plan → execute → kernel → propose. Nothing is written.

    A change set with `status="failed"` is a real answer, not an error: the planner could
    not produce something the kernel would accept, and `rejected[].reasons` says exactly
    why in the kernel's own words (HR-3).
    """
    svc = services(request)
    document = await load_document(svc, doc_id, payload.version)

    change_set = await svc.command_loop().run(document, payload.command)
    svc.change_set_store().put(change_set)
    return change_set


@router.get("/change-sets/{change_set_id}", response_model=ProposedChangeSet)
async def get_change_set(request: Request, change_set_id: str) -> ProposedChangeSet:
    return services(request).change_set_store().get(change_set_id)


@router.get("/documents/{doc_id}/change-sets", response_model=list[ProposedChangeSet])
async def list_change_sets(request: Request, doc_id: str) -> list[ProposedChangeSet]:
    return services(request).change_set_store().for_document(doc_id)


@router.post("/change-sets/{change_set_id}/approve", response_model=CommitResult)
async def approve(request: Request, change_set_id: str, payload: ApprovalPayload) -> CommitResult:
    """Commit the approved subset. Returns 409 if a citation is still waiting on a decision
    — an undecided orphaned anchor is not something this endpoint resolves on the user's
    behalf in either direction (ADR-013 step 4)."""
    svc = services(request)
    change_set = svc.change_set_store().get(change_set_id)

    if change_set.status == "failed":
        raise HTTPException(
            status_code=409,
            detail=(
                "this change set produced no valid changes, so there is nothing to approve. "
                "See `rejected` for the kernel's reasons."
            ),
        )

    result = await svc.versions().commit(
        change_set,
        ApprovalRequest(
            change_set_id=change_set_id,
            approved_change_ids=payload.approved_change_ids,
            rejected_change_ids=payload.rejected_change_ids,
            orphan_decisions=payload.orphan_decisions,
        ),
    )
    if result.committed:
        svc.change_set_store().discard(change_set_id)
    return result


@router.get("/agent/metrics", response_model=OperationMetrics)
async def metrics() -> OperationMetrics:
    """The ADR-009 tripwire, exposed rather than buried. `tripped` means FreeformEdit is
    firing above ~20% of commands, and the fix is a better typed vocabulary."""
    return METRICS.snapshot()


__all__ = ["router"]
