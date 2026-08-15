"""Approval and versioning.

**Nothing is committed without explicit user approval.** A `ProposedChangeSet` sits in the
store until the user names the changes they accept and resolves any citation left waiting
on a decision. Only then does a new IR version exist.

Two things are worth stating plainly, because they are what make the guarantee real rather
than procedural:

* **The kernel runs again at commit time**, against the document as it actually is at that
  moment, with the user's approvals and orphan decisions folded in. Approving a subset of a
  change set produces a different document than the one the kernel judged when the set was
  proposed, and re-judging is the only honest response to that.
* **A removal is only ever a removal the user asked for.** The one place `approved_removals`
  is populated from a user action is here, and it is populated from an explicit
  `OrphanDecision(action="remove")` naming a specific anchor.

Versions are copy-on-write through B1's store, so every version is revertible: reverting is
committing a copy of an older version, not mutating anything.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.diff import StructuralDiff, build_diff
from app.agent.fragment import iter_spans, source_multiset
from app.agent.kernel import ChangeContext, InvariantKernel
from app.agent.loop import EvaluatedChange, ProposedChangeSet
from app.agent.ports import DocumentStore
from app.core.contracts import (
    CitationAnchor,
    Document,
    KernelRejection,
    KernelVerdict,
    Operation,
    ProposedChange,
    Span,
)

log = logging.getLogger("app.agent.versioning")


class OrphanDecision(BaseModel):
    """The user's answer for an anchor that found no home. There is no fourth option, and
    in particular there is no "leave it to the system"."""

    anchor_id: str
    action: Literal["keep", "move", "remove"]
    target_span_id: str | None = None  # required for "move"


class ApprovalRequest(BaseModel):
    change_set_id: str
    approved_change_ids: list[str] = Field(default_factory=list)
    rejected_change_ids: list[str] = Field(default_factory=list)
    orphan_decisions: list[OrphanDecision] = Field(default_factory=list)


class CommitResult(BaseModel):
    committed: bool
    doc_id: str
    base_version: int
    new_version: int | None = None
    applied_change_ids: list[str] = Field(default_factory=list)
    skipped: dict[str, str] = Field(default_factory=dict)
    """change_id → why it could not be applied after the user's other choices. A change
    the user approved that then fails to apply is reported, never dropped quietly (HR-3)."""
    diff: StructuralDiff | None = None
    verdict: KernelVerdict | None = None
    message: str


class ApprovalError(RuntimeError):
    """The approval request itself is malformed. Surfaced to the user, never guessed at."""


class VersionService:
    def __init__(self, documents: DocumentStore, kernel: InvariantKernel) -> None:
        self._documents = documents
        self._kernel = kernel

    # ------------------------------------------------------------------ commit

    async def commit(
        self, change_set: ProposedChangeSet, request: ApprovalRequest
    ) -> CommitResult:
        if request.change_set_id != change_set.change_set_id:
            raise ApprovalError(
                f"approval names change set {request.change_set_id!r} but "
                f"{change_set.change_set_id!r} was supplied"
            )

        base = await self._documents.get(change_set.doc_id, change_set.base_version)
        if base is None:
            raise ApprovalError(
                f"document {change_set.doc_id} v{change_set.base_version} no longer exists"
            )

        by_id = {change.change_id: change for change in change_set.changes}
        unknown = [cid for cid in request.approved_change_ids if cid not in by_id]
        if unknown:
            raise ApprovalError(f"approval names change ids that are not in this set: {unknown}")

        approved = [by_id[cid] for cid in request.approved_change_ids]
        if not approved:
            return CommitResult(
                committed=False,
                doc_id=change_set.doc_id,
                base_version=change_set.base_version,
                message="No changes were approved, so no version was written.",
            )

        self._check_orphans_resolved(approved, request.orphan_decisions)

        working = base
        applied: list[str] = []
        skipped: dict[str, str] = {}
        last_verdict: KernelVerdict | None = None

        for change in approved:
            verdict = self._kernel.evaluate(
                before=working, change=change.change, context=change.context
            )
            if verdict.decision == "reject":
                # Re-judged against the document as it is now, this change no longer holds.
                skipped[change.change_id] = "; ".join(verdict.reasons)
                continue
            working = self._kernel.project(working, change.change)
            applied.append(change.change_id)
            last_verdict = verdict

        working, orphan_verdict = await self._apply_orphan_decisions(
            working, approved, request.orphan_decisions
        )
        last_verdict = orphan_verdict or last_verdict

        self._assert_multiset_preserved(base, working, request.orphan_decisions, approved)

        if not applied and not request.orphan_decisions:
            return CommitResult(
                committed=False,
                doc_id=change_set.doc_id,
                base_version=change_set.base_version,
                skipped=skipped,
                message=(
                    "None of the approved changes could be applied to the current version. "
                    "Their reasons are listed; nothing was written."
                ),
            )

        stored = await self._documents.put_version(working, parent_version=base.version)
        log.info(
            "committed %s v%d → v%d (%d change(s), %d skipped)",
            stored.doc_id,
            base.version,
            stored.version,
            len(applied),
            len(skipped),
        )

        return CommitResult(
            committed=True,
            doc_id=stored.doc_id,
            base_version=base.version,
            new_version=stored.version,
            applied_change_ids=applied,
            skipped=skipped,
            diff=build_diff(base, stored),
            verdict=last_verdict,
            message=(
                f"Committed version {stored.version}. "
                + (f"{len(skipped)} approved change(s) no longer applied and were not written. "
                   if skipped else "")
                + "This version is revertible."
            ),
        )

    @staticmethod
    def _assert_multiset_preserved(
        base: Document,
        final: Document,
        decisions: list[OrphanDecision],
        approved: list[EvaluatedChange],
    ) -> None:
        """The end-to-end HR-5 statement, checked once more at the commit boundary.

        The kernel already judged each change individually, and each judgement was correct
        for the document it saw. This checks the composition of them — base version against
        the bytes about to be written — because HR-5 is a claim about the document, not
        about any one step that produced it.
        """
        lost = source_multiset(base) - source_multiset(final)
        if not lost:
            return

        removed_anchor_ids = {d.anchor_id for d in decisions if d.action == "remove"}
        allowed: Counter[str] = Counter()
        for change in approved:
            for orphan in change.orphans:
                if orphan.anchor_id in removed_anchor_ids:
                    allowed.update(orphan.source_ids)
            for source_id, count in change.context.approved_removals.items():
                allowed[source_id] += count

        unapproved = lost - allowed
        if unapproved:
            raise KernelRejection(
                "refusing to write this version: it would drop "
                + ", ".join(f"{count}× {source_id}" for source_id, count in sorted(unapproved.items()))
                + " without an approved removal (HR-5). No version was written."
            )

    # ------------------------------------------------------------------ revert

    async def revert(self, doc_id: str, to_version: int) -> CommitResult:
        """Reverting writes a *new* version whose content is the old one. History is
        append-only, so a revert is itself revertible."""
        target = await self._documents.get(doc_id, to_version)
        if target is None:
            raise ApprovalError(f"document {doc_id} has no version {to_version}")

        current = await self._documents.get(doc_id)
        if current is None:
            raise ApprovalError(f"document {doc_id} does not exist")
        if current.version == to_version:
            return CommitResult(
                committed=False,
                doc_id=doc_id,
                base_version=current.version,
                message=f"Version {to_version} is already the current version.",
            )

        stored = await self._documents.put_version(target, parent_version=current.version)
        return CommitResult(
            committed=True,
            doc_id=doc_id,
            base_version=current.version,
            new_version=stored.version,
            diff=build_diff(current, stored),
            message=f"Reverted the content of version {to_version} into new version {stored.version}.",
        )

    # ------------------------------------------------------------------ orphans

    @staticmethod
    def _check_orphans_resolved(
        approved: list[EvaluatedChange], decisions: list[OrphanDecision]
    ) -> None:
        """An unresolved orphan blocks the commit. It does not default to "keep", and it
        certainly does not default to "remove" (HR-5, ADR-013 step 4)."""
        pending = {
            orphan.anchor_id for change in approved for orphan in change.orphans
        }
        decided = {decision.anchor_id for decision in decisions}
        unresolved = pending - decided
        if unresolved:
            raise ApprovalError(
                f"{len(unresolved)} citation(s) are still waiting on your decision "
                f"({sorted(unresolved)}). Choose keep, move, or remove for each before committing."
            )
        stray = decided - pending
        if stray:
            raise ApprovalError(
                f"orphan decisions name anchors that are not waiting on a decision: {sorted(stray)}"
            )
        for decision in decisions:
            if decision.action == "move" and not decision.target_span_id:
                raise ApprovalError(
                    f"the decision to move anchor {decision.anchor_id} needs a target_span_id"
                )

    async def _apply_orphan_decisions(
        self,
        working: Document,
        approved: list[EvaluatedChange],
        decisions: list[OrphanDecision],
    ) -> tuple[Document, KernelVerdict | None]:
        if not decisions:
            return working, None

        orphans = {
            orphan.anchor_id: (orphan, change)
            for change in approved
            for orphan in change.orphans
        }
        spans = {span.id: span for _s, _b, span in iter_spans(working)}
        replace_spans: dict[str, Span] = {}
        removals: Counter[str] = Counter()
        placed: list[str] = []

        for decision in decisions:
            orphan, _change = orphans[decision.anchor_id]

            if decision.action == "remove":
                removals.update(orphan.source_ids)
                continue

            target_id = decision.target_span_id if decision.action == "move" else orphan.best_span_id
            if target_id is None:
                raise ApprovalError(
                    f"anchor {decision.anchor_id} has nowhere to be kept — it did not land near any "
                    f"sentence. Choose 'move' with a target span, or 'remove'."
                )
            target = spans.get(target_id)
            if target is None:
                raise ApprovalError(
                    f"span {target_id!r} does not exist in the document being committed"
                )

            updated = replace_spans.get(target_id) or target.model_copy(deep=True)
            updated.citation_anchors = [
                *updated.citation_anchors,
                _reattached(orphan, len(updated.text)),
            ]
            replace_spans[target_id] = updated
            placed.append(decision.anchor_id)

        change = ProposedChange(
            change_id="orphan-decisions",
            op=Operation.model_validate(
                {"op": "FreeformEdit", "target_ids": sorted(replace_spans) or ["<removals>"],
                 "params": {"instruction": "apply the user's citation decisions"},
                 "no_typed_op_applies": True,
                 "justification": "user-directed citation placement, not a model edit"}
            ),
            new_fragment={"replace_spans": [span.model_dump() for span in replace_spans.values()]},
            new_source_ids=[],
            orphaned_anchor_ids=[],
            rationale=(
                f"Applied your decisions: {len(placed)} citation(s) placed, "
                f"{sum(removals.values())} removed."
            ),
        )
        verdict = self._kernel.evaluate(
            before=working,
            change=change,
            context=ChangeContext(approved_removals=dict(removals)),
        )
        if verdict.decision == "reject":
            raise KernelRejection(
                "your citation decisions could not be applied: " + "; ".join(verdict.reasons)
            )
        return self._kernel.project(working, change), verdict


def _reattached(orphan, offset: int) -> CitationAnchor:  # noqa: ANN001
    return CitationAnchor(
        anchor_id=orphan.anchor_id,
        source_ids=list(orphan.source_ids),
        offset_in_span=offset,
        original_marker_text=orphan.marker,
        provenance_kind="parsed",
        confidence=orphan.score if orphan.score is not None else 0.0,
    )


__all__ = [
    "ApprovalError",
    "ApprovalRequest",
    "CommitResult",
    "OrphanDecision",
    "VersionService",
]
