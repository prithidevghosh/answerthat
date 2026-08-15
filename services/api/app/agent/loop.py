"""The command loop: plan → execute → kernel → (retry ≤ 2) → propose.

Three properties this loop must have, and the reasons they are not negotiable:

* **A rejection goes back to the planner with its reason.** The reason is the only thing
  the planner can learn from, and it is the same string the user will read if the retries
  run out (HR-3).
* **At most two retries.** A loop that retries until something passes will eventually get
  something past by luck, which is worse than failing.
* **Nothing unvalidated is ever applied.** When the retries are exhausted, what survives is
  exactly the set of changes the kernel accepted or flagged — never a fallback, never a
  partial application of a rejected operation.

Nothing here commits anything. The output is a proposal awaiting explicit user approval.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.diff import StructuralDiff, build_diff
from app.agent.executor import OperationExecutor
from app.agent.kernel import ChangeContext, InvariantKernel, ReattachmentRecord
from app.agent.metrics import METRICS, MetricsRegistry
from app.agent.planner import Planner, PlanningError, validate_plan
from app.core.contracts import Document, EditPlan, KernelVerdict, Operation, ProposedChange

log = logging.getLogger("app.agent.loop")

MAX_RETRIES = 2


OrphanAction = Literal["keep", "move", "remove"]


def _all_orphan_actions() -> list[OrphanAction]:
    """Every anchor gets the same three choices, and "leave it to the system" is not one
    of them (ADR-013 step 4)."""
    return ["keep", "move", "remove"]


class OrphanOption(BaseModel):
    """An anchor that found no home, presented as a decision rather than a deletion."""

    anchor_id: str
    marker: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    best_span_id: str | None = None
    best_span_text: str | None = None
    score: float | None = None
    threshold: float | None = None
    actions: list[OrphanAction] = Field(default_factory=_all_orphan_actions)


class EvaluatedChange(BaseModel):
    change: ProposedChange
    verdict: KernelVerdict
    context: ChangeContext
    diff: StructuralDiff
    notes: list[str] = Field(default_factory=list)
    orphans: list[OrphanOption] = Field(default_factory=list)

    @property
    def change_id(self) -> str:
        return self.change.change_id


class RejectedOperation(BaseModel):
    operation: Operation | None = None  # None → the plan itself was malformed
    reasons: list[str]
    attempt: int


class ProposedChangeSet(BaseModel):
    change_set_id: str
    doc_id: str
    base_version: int
    command: str
    plan_id: str | None = None
    status: Literal["awaiting_approval", "failed"]
    attempts: int
    changes: list[EvaluatedChange] = Field(default_factory=list)
    rejected: list[RejectedOperation] = Field(default_factory=list)
    message: str | None = None

    @property
    def has_unresolved_orphans(self) -> bool:
        return any(change.orphans for change in self.changes)


class CommandLoop:
    def __init__(
        self,
        *,
        planner: Planner,
        executor: OperationExecutor,
        kernel: InvariantKernel,
        metrics: MetricsRegistry | None = None,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._kernel = kernel
        self._metrics = metrics or METRICS
        self._max_retries = max_retries

    async def run(self, document: Document, command: str) -> ProposedChangeSet:
        change_set_id = f"cs-{uuid.uuid4().hex[:12]}"
        feedback: list[str] = []
        previous_plan: EditPlan | None = None
        last_rejections: list[RejectedOperation] = []
        last_changes: list[EvaluatedChange] = []
        last_plan_id: str | None = None

        for attempt in range(self._max_retries + 1):
            try:
                plan = await self._planner.plan(
                    command=command,
                    document=document,
                    rejection_feedback=feedback or None,
                    previous_plan=previous_plan,
                )
            except PlanningError as exc:
                return self._failed(change_set_id, document, command, attempt + 1, str(exc))

            last_plan_id = plan.plan_id
            problems = validate_plan(plan, document)
            if problems:
                log.info("plan %s failed pre-flight on attempt %d: %s", plan.plan_id, attempt, problems)
                feedback, previous_plan = problems, plan
                last_rejections = [
                    RejectedOperation(operation=op, reasons=problems, attempt=attempt)
                    for op in plan.operations
                ] or [RejectedOperation(reasons=problems, attempt=attempt)]
                last_changes = []
                continue

            changes, rejections = await self._execute_plan(document, plan, attempt)
            last_changes, last_rejections = changes, rejections

            if not rejections:
                self._metrics.record_plan(plan)
                return ProposedChangeSet(
                    change_set_id=change_set_id,
                    doc_id=document.doc_id,
                    base_version=document.version,
                    command=command,
                    plan_id=plan.plan_id,
                    status="awaiting_approval",
                    attempts=attempt + 1,
                    changes=changes,
                    message=_summary(changes),
                )

            feedback = [reason for rejection in rejections for reason in rejection.reasons]
            previous_plan = plan
            log.info(
                "attempt %d/%d: %d operation(s) rejected, returning reasons to the planner",
                attempt + 1,
                self._max_retries + 1,
                len(rejections),
            )

        # Retries exhausted. Everything that survives here passed the kernel; everything
        # that didn't is surfaced with its reason, unaltered.
        if previous_plan is not None:
            self._metrics.record_plan(previous_plan)

        return ProposedChangeSet(
            change_set_id=change_set_id,
            doc_id=document.doc_id,
            base_version=document.version,
            command=command,
            plan_id=last_plan_id,
            status="awaiting_approval" if last_changes else "failed",
            attempts=self._max_retries + 1,
            changes=last_changes,
            rejected=last_rejections,
            message=(
                f"{len(last_rejections)} operation(s) could not be validated after "
                f"{self._max_retries} retries. Their reasons are shown below and nothing from them "
                f"was applied."
            ),
        )

    # ------------------------------------------------------------------ internals

    async def _execute_plan(
        self, document: Document, plan: EditPlan, attempt: int
    ) -> tuple[list[EvaluatedChange], list[RejectedOperation]]:
        working = document
        changes: list[EvaluatedChange] = []
        rejections: list[RejectedOperation] = []

        for operation in plan.operations:
            execution = await self._executor.execute(working, operation)
            if not execution.ok:
                rejections.append(
                    RejectedOperation(
                        operation=operation, reasons=[execution.error or "unknown"], attempt=attempt
                    )
                )
                continue

            change = execution.change
            assert change is not None
            await warm_sources_for(self._kernel, change)
            verdict = self._kernel.evaluate(before=working, change=change, context=execution.context)

            if verdict.decision == "reject":
                rejections.append(
                    RejectedOperation(operation=operation, reasons=verdict.reasons, attempt=attempt)
                )
                continue

            projected = self._kernel.project(working, change)
            changes.append(
                EvaluatedChange(
                    change=change,
                    verdict=verdict,
                    context=execution.context,
                    diff=build_diff(working, projected, held_anchor_ids=change.orphaned_anchor_ids),
                    notes=execution.notes,
                    orphans=_orphan_options(working, projected, change, execution.context),
                )
            )
            working = projected

        return changes, rejections

    @staticmethod
    def _failed(
        change_set_id: str, document: Document, command: str, attempts: int, message: str
    ) -> ProposedChangeSet:
        return ProposedChangeSet(
            change_set_id=change_set_id,
            doc_id=document.doc_id,
            base_version=document.version,
            command=command,
            status="failed",
            attempts=attempts,
            message=message,
        )


async def warm_sources_for(kernel: InvariantKernel, change: ProposedChange) -> None:
    """Index every `source_id` the kernel is about to check, before it checks it.

    Called on the two paths that reach `evaluate`: here and `VersionService.commit`. The
    ids a change *claims* are exactly the ids we warm, fabricated ones included — those come
    back known-absent and REJECT rule 1 fires on a real answer rather than on an exception.
    """
    warm = getattr(kernel.sources, "warm", None)
    if warm is None:
        return
    ids = kernel.referenced_source_ids(change)
    if ids:
        await warm(ids)


def _orphan_options(
    before: Document,
    after: Document,
    change: ProposedChange,
    context: ChangeContext,
) -> list[OrphanOption]:
    from app.agent.fragment import anchors_by_id, iter_spans

    if not change.orphaned_anchor_ids:
        return []

    before_anchors = anchors_by_id(before)
    after_spans = {span.id: span for _s, _b, span in iter_spans(after)}
    records = {record.anchor_id: record for record in context.reattachments}

    options: list[OrphanOption] = []
    for anchor_id in change.orphaned_anchor_ids:
        entry = before_anchors.get(anchor_id)
        if entry is None:
            continue
        _span_id, anchor = entry
        record: ReattachmentRecord | None = records.get(anchor_id)
        best_span = after_spans.get(record.best_span_id) if record and record.best_span_id else None
        options.append(
            OrphanOption(
                anchor_id=anchor_id,
                marker=anchor.original_marker_text,
                source_ids=list(anchor.source_ids),
                best_span_id=best_span.id if best_span else None,
                best_span_text=best_span.text if best_span else None,
                score=record.score if record else None,
                threshold=record.threshold if record else None,
            )
        )
    return options


def _summary(changes: list[EvaluatedChange]) -> str:
    if not changes:
        return "No change was produced."
    flagged = sum(1 for change in changes if change.verdict.decision == "flag")
    orphans = sum(len(change.orphans) for change in changes)
    parts = [f"{len(changes)} change(s) proposed"]
    if flagged:
        parts.append(f"{flagged} carrying warnings")
    if orphans:
        parts.append(f"{orphans} citation(s) waiting on your decision")
    return ", ".join(parts) + ". Nothing is applied until you approve it."


__all__ = [
    "CommandLoop",
    "EvaluatedChange",
    "MAX_RETRIES",
    "OrphanOption",
    "ProposedChangeSet",
    "RejectedOperation",
]
