"""Deterministic execution of typed operations.

The planner chooses *what* to do; this module decides *how*, using ordinary code. Every
executor produces a `ProposedChange` plus the `ChangeContext` evidence the kernel needs to
judge it — and nothing here writes a document version. An executor's output is a proposal,
full stop.

Two invariants hold across all seven operations:

* a `source_id` only ever arrives from B2's retrieval, which returns ids its adapters
  already wrote to the append-only store (HR-1). No executor constructs one.
* text rewriting always goes through `DetachTransformReattach` (HR-5 / ADR-013). No
  executor asks a model to edit text with citations in it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.agent import prompts
from app.agent.fragment import iter_blocks, iter_spans
from app.agent.kernel import ChangeContext
from app.agent.operations import (
    AddCitationsParams,
    FindSupportParams,
    FreeformEditParams,
    MoveTextParams,
    OperationSpecError,
    ReplaceCitationParams,
    RewriteSectionParams,
    ShortenParams,
    parse_params,
)
from app.agent.ports import (
    ClaimExtractor,
    Embedder,
    FingerprintStore,
    RetrievalService,
    SourceReader,
    TextModel,
    VerificationService,
)
from app.agent.thresholds import ReattachmentBand
from app.agent.transform import DetachTransformReattach, TransformReport, host_sentence_for
from app.core.contracts import (
    Block,
    CitationAnchor,
    Claim,
    Document,
    Operation,
    OperationType,
    ProposedChange,
    Verification,
    VerificationLabel,
)

log = logging.getLogger("app.agent.executor")

GROUNDING_LABELS = frozenset({VerificationLabel.SUPPORTS, VerificationLabel.PARTIALLY_SUPPORTS})


class ExecutionError(RuntimeError):
    """An operation could not be executed. Carries a message written for the planner and
    the user to read. Never swallowed, never turned into an empty change (HR-3)."""


@dataclass
class Execution:
    """One operation's proposal, or the reason there isn't one."""

    change: ProposedChange | None = None
    context: ChangeContext = field(default_factory=ChangeContext)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.change is not None and self.error is None


# --------------------------------------------------------------------------- targets


def resolve_blocks(document: Document, target_ids: list[str]) -> list[Block]:
    """Expand section / block / span ids to the blocks they name, in document order.

    An unknown id is an error, not an empty result — the planner hallucinating a target is
    exactly the thing we want to hand straight back to it.
    """
    wanted = set(target_ids)
    section_ids = {s.id for s in document.sections}
    block_ids = {b.id for _s, b in iter_blocks(document)}
    span_to_block = {sp.id: b.id for _s, b, sp in iter_spans(document)}

    unknown = wanted - section_ids - block_ids - set(span_to_block)
    if unknown:
        raise ExecutionError(
            f"target_ids {sorted(unknown)} do not exist in this document. Use ids from the "
            f"document outline."
        )

    selected: list[str] = []
    for section in document.sections:
        for block in section.blocks:
            if (
                section.id in wanted
                or block.id in wanted
                or any(span_to_block.get(sid) == block.id for sid in wanted)
            ):
                selected.append(block.id)

    by_id = {b.id: b for _s, b in iter_blocks(document)}
    return [by_id[bid] for bid in selected]


def _section_of(document: Document, block_id: str) -> str:
    for section in document.sections:
        if any(b.id == block_id for b in section.blocks):
            return section.id
    raise ExecutionError(f"block {block_id!r} is not in any section")


# --------------------------------------------------------------------------- executor


class OperationExecutor:
    def __init__(
        self,
        *,
        sources: SourceReader,
        retrieval: RetrievalService,
        verifier: VerificationService,
        claims: ClaimExtractor,
        embedder: Embedder,
        text_model: TextModel,
        fingerprints: FingerprintStore,
        band: ReattachmentBand,
    ) -> None:
        self._sources = sources
        self._retrieval = retrieval
        self._verifier = verifier
        self._claims = claims
        self._dtr = DetachTransformReattach(embedder, text_model, fingerprints, band=band)

    async def execute(self, document: Document, operation: Operation) -> Execution:
        try:
            params = parse_params(operation)
        except OperationSpecError as exc:
            return Execution(error=str(exc))

        handlers: dict[OperationType, Any] = {
            OperationType.ADD_CITATIONS: self._add_citations,
            OperationType.FIND_SUPPORT: self._find_support,
            OperationType.SHORTEN: self._shorten,
            OperationType.REWRITE_SECTION: self._rewrite_section,
            OperationType.REPLACE_CITATION: self._replace_citation,
            OperationType.MOVE_TEXT: self._move_text,
            OperationType.FREEFORM_EDIT: self._freeform_edit,
        }

        try:
            return await handlers[operation.op](document, operation, params)
        except ExecutionError as exc:
            return Execution(error=str(exc))

    # ------------------------------------------------------------------ citation ops

    async def _add_citations(
        self, document: Document, operation: Operation, params: AddCitationsParams
    ) -> Execution:
        blocks = resolve_blocks(document, operation.target_ids)
        if not blocks:
            raise ExecutionError("AddCitations matched no blocks in the target")

        claims = await self._claims.extract(document, [b.id for b in blocks])
        uncited = [c for c in claims if not c.anchor_ids]
        if not uncited:
            raise ExecutionError(
                "every claim in the target already carries a citation — nothing to add. "
                "Use FindSupport to check whether the existing citations actually support them."
            )

        ranked = sorted(uncited, key=lambda c: c.citability, reverse=True)[: params.count]
        return await self._anchor_claims(document, operation, ranked, params.count, params.criteria)

    async def _find_support(
        self, document: Document, operation: Operation, params: FindSupportParams
    ) -> Execution:
        blocks = resolve_blocks(document, operation.target_ids)
        claims = await self._claims.extract(document, [b.id for b in blocks])
        if params.claim_ids:
            wanted = set(params.claim_ids)
            claims = [c for c in claims if c.claim_id in wanted]
            missing = wanted - {c.claim_id for c in claims}
            if missing:
                raise ExecutionError(f"claim_ids {sorted(missing)} were not found in the target")
        if not claims:
            raise ExecutionError("FindSupport found no claims in the target")
        return await self._anchor_claims(document, operation, claims[: params.count], params.count, None)

    async def _anchor_claims(
        self,
        document: Document,
        operation: Operation,
        claims: list[Claim],
        per_claim: int,
        criteria: str | None,
    ) -> Execution:
        """Retrieve → verify → propose anchors. Asserts no new text: the spans keep their
        prose exactly and only gain anchors, so REJECT rule 3 has nothing to fire on."""
        spans = {sp.id: (b, sp) for _s, b, sp in iter_spans(document)}
        additions: dict[str, list[CitationAnchor]] = {}
        verifications: dict[str, Verification] = {}
        new_source_ids: list[str] = []
        notes: list[str] = []

        for claim in claims:
            entry = spans.get(claim.span_id)
            if entry is None:
                notes.append(f"claim {claim.claim_id}: span {claim.span_id} is no longer in the document")
                continue

            candidates = await self._retrieval.find_candidates(claim, limit=max(per_claim * 3, 5))
            if not candidates:
                notes.append(
                    f"claim {claim.claim_id!r}: no candidate sources found. This is reported, not "
                    f"hidden — the claim stays uncited."
                )
                continue

            accepted = 0
            attempted: list[str] = []
            for source_id in candidates:
                if accepted >= 1:
                    break
                attempted.append(source_id)
                verification = await self._verifier.verify(claim.text, source_id)
                if verification.label not in GROUNDING_LABELS:
                    continue

                _block, span = entry
                anchor_id = f"{claim.claim_id}::a{accepted}"
                anchor = CitationAnchor(
                    anchor_id=anchor_id,
                    source_ids=[source_id],
                    offset_in_span=len(span.text),
                    original_marker_text=None,
                    provenance_kind="agent_added",
                    confidence=verification.confidence,
                )
                additions.setdefault(span.id, []).append(anchor)
                verifications[anchor_id] = verification
                new_source_ids.append(source_id)
                accepted += 1

            if accepted == 0:
                notes.append(
                    f"claim {claim.claim_id!r}: {len(attempted)} candidate(s) checked, none verified as "
                    f"supporting or partially supporting. No citation added."
                )

        if not additions:
            raise ExecutionError(
                "no citation could be added: "
                + ("; ".join(notes) if notes else "retrieval and verification produced nothing")
            )

        replace_spans = []
        for span_id, anchors in additions.items():
            _block, span = spans[span_id]
            updated = span.model_copy(deep=True)
            updated.citation_anchors = [*updated.citation_anchors, *anchors]
            replace_spans.append(updated.model_dump())

        change = ProposedChange(
            change_id=f"{operation.op.value}:{'+'.join(sorted(additions))}",
            op=operation,
            new_fragment={"replace_spans": replace_spans},
            new_source_ids=sorted(set(new_source_ids)),
            orphaned_anchor_ids=[],
            rationale=_rationale(
                f"Added {len(verifications)} verified citation(s) across {len(additions)} span(s)",
                criteria,
                notes,
            ),
        )
        return Execution(change=change, context=ChangeContext(verifications=verifications), notes=notes)

    async def _replace_citation(
        self, document: Document, operation: Operation, params: ReplaceCitationParams
    ) -> Execution:
        target = next(
            (
                (block, span, anchor)
                for _s, block, span in iter_spans(document)
                for anchor in span.citation_anchors
                if anchor.anchor_id == params.anchor_id
            ),
            None,
        )
        if target is None:
            raise ExecutionError(f"anchor {params.anchor_id!r} does not exist in this document")

        _block, span, anchor = target
        old = params.old_source_id or (anchor.source_ids[0] if len(anchor.source_ids) == 1 else None)
        if old is None:
            raise ExecutionError(
                f"anchor {params.anchor_id!r} carries {len(anchor.source_ids)} sources; "
                f"name which one to replace via old_source_id"
            )
        if old not in anchor.source_ids:
            raise ExecutionError(f"anchor {params.anchor_id!r} does not cite {old!r}")
        # B2's sync `has` answers from an index that must be warmed first; an unwarmed id
        # raises rather than reporting absence (memory.md §5, B2 → B3).
        warm = getattr(self._sources, "warm", None)
        if warm is not None:
            await warm([params.new_source_id])
        if not self._sources.has(params.new_source_id):
            raise ExecutionError(
                f"source_id {params.new_source_id!r} is not in the source store. Only retrieval "
                f"can introduce a source; it cannot be named directly."
            )

        claim_text = host_sentence_for(span.text, anchor.offset_in_span)
        verification = await self._verifier.verify(claim_text, params.new_source_id)

        updated_anchor = anchor.model_copy(
            update={
                "source_ids": [params.new_source_id if s == old else s for s in anchor.source_ids],
                "provenance_kind": "agent_added",
                "confidence": verification.confidence,
            }
        )
        updated_span = span.model_copy(deep=True)
        updated_span.citation_anchors = [
            updated_anchor if a.anchor_id == anchor.anchor_id else a for a in updated_span.citation_anchors
        ]

        change = ProposedChange(
            change_id=f"ReplaceCitation:{params.anchor_id}",
            op=operation,
            new_fragment={"replace_spans": [updated_span.model_dump()]},
            new_source_ids=[params.new_source_id],
            orphaned_anchor_ids=[],
            rationale=(
                f"Replaced {old} with {params.new_source_id} at anchor {params.anchor_id}, keeping the "
                f"citation in place. Verifier: {verification.label.value}."
            ),
        )
        # The user named this swap, so the removal of `old` is the removal they asked for.
        # It still cannot commit without their approval of the change set itself.
        return Execution(
            change=change,
            context=ChangeContext(
                verifications={updated_anchor.anchor_id: verification},
                approved_removals={old: 1},
            ),
        )

    # ------------------------------------------------------------------ text transforms

    async def _shorten(self, document: Document, operation: Operation, params: ShortenParams) -> Execution:
        blocks = resolve_blocks(document, operation.target_ids)
        if not blocks:
            raise ExecutionError("Shorten matched no blocks in the target")

        def instruction_for(block: Block) -> str:
            words = sum(len(span.text.split()) for span in block.spans)
            return prompts.shorten_instruction(words, params.ratio)

        return await self._run_transform(document, operation, blocks, instruction_for, "Shortened")

    async def _rewrite_section(
        self, document: Document, operation: Operation, params: RewriteSectionParams
    ) -> Execution:
        blocks = resolve_blocks(document, operation.target_ids)
        if not blocks:
            raise ExecutionError("RewriteSection matched no blocks in the target")
        instruction = prompts.REWRITE_INSTRUCTION.format(instruction=params.instruction)
        return await self._run_transform(document, operation, blocks, lambda _b: instruction, "Rewrote")

    async def _freeform_edit(
        self, document: Document, operation: Operation, params: FreeformEditParams
    ) -> Execution:
        blocks = resolve_blocks(document, operation.target_ids)
        if not blocks:
            raise ExecutionError("FreeformEdit matched no blocks in the target")
        log.info(
            "FreeformEdit fired on %d block(s): %s", len(blocks), operation.justification
        )
        instruction = prompts.FREEFORM_INSTRUCTION.format(instruction=params.instruction)
        execution = await self._run_transform(
            document, operation, blocks, lambda _b: instruction, "Applied a freeform edit to"
        )
        if execution.change is not None:
            execution.notes.append(
                "FreeformEdit: only the generic invariants apply to this change (ADR-009). "
                f"Justification: {operation.justification}"
            )
        return execution

    async def _run_transform(
        self,
        document: Document,
        operation: Operation,
        blocks: list[Block],
        instruction_for,
        verb: str,
    ) -> Execution:
        replace_blocks: list[dict] = []
        derived: dict[str, list[str]] = {}
        reattachments = []
        orphaned: list[CitationAnchor] = []
        notes: list[str] = []

        for block in blocks:
            report: TransformReport = await self._dtr.run(
                block,
                system=prompts.TEXT_MODEL_SYSTEM,
                instruction=instruction_for(block),
            )
            if not report.new_spans:
                raise ExecutionError(
                    f"the text model returned nothing usable for block {block.id!r}; no change is "
                    f"proposed and the block is untouched"
                )

            rebuilt = block.model_copy(deep=True)
            rebuilt.spans = report.new_spans
            replace_blocks.append(rebuilt.model_dump())
            derived.update(report.derived_spans)
            reattachments.extend(report.reattachments)
            orphaned.extend(report.orphaned_anchors)
            if report.marker_leaks:
                notes.append(
                    f"block {block.id}: the text model emitted {len(report.marker_leaks)} "
                    f"citation-marker-shaped string(s) ({report.marker_leaks[:3]}); they were removed "
                    f"and the real anchors were reattached from their fingerprints."
                )

        if orphaned:
            notes.append(
                f"{len(orphaned)} citation(s) could not be placed confidently and are waiting on your "
                f"decision: keep, move, or remove."
            )

        change = ProposedChange(
            change_id=f"{operation.op.value}:{'+'.join(b.id for b in blocks)}",
            op=operation,
            new_fragment={"replace_blocks": replace_blocks},
            new_source_ids=[],
            orphaned_anchor_ids=[a.anchor_id for a in orphaned],
            rationale=_rationale(f"{verb} {len(blocks)} block(s)", None, notes),
        )
        return Execution(
            change=change,
            context=ChangeContext(derived_spans=derived, reattachments=reattachments),
            notes=notes,
        )

    # ------------------------------------------------------------------ structural

    async def _move_text(
        self, document: Document, operation: Operation, params: MoveTextParams
    ) -> Execution:
        blocks = resolve_blocks(document, operation.target_ids)
        if not blocks:
            raise ExecutionError("MoveText matched no blocks in the target")
        if params.to_section_id not in {s.id for s in document.sections}:
            raise ExecutionError(f"destination section {params.to_section_id!r} does not exist")

        moves = []
        after = params.after_block_id
        for block in blocks:
            moves.append(
                {
                    "block_id": block.id,
                    "to_section_id": params.to_section_id,
                    "after_block_id": after,
                }
            )
            after = block.id  # keep the moved blocks in their original relative order

        origins = {_section_of(document, b.id) for b in blocks}
        change = ProposedChange(
            change_id=f"MoveText:{'+'.join(b.id for b in blocks)}",
            op=operation,
            new_fragment={"move_blocks": moves},
            new_source_ids=[],
            orphaned_anchor_ids=[],
            rationale=(
                f"Moved {len(blocks)} block(s) from {sorted(origins)} to {params.to_section_id}. "
                f"Citation anchors travel with the text, so the multiset is unchanged."
            ),
        )
        return Execution(change=change, context=ChangeContext())


def _rationale(headline: str, criteria: str | None, notes: list[str]) -> str:
    parts = [headline]
    if criteria:
        parts.append(f"Criteria: {criteria}.")
    parts.extend(notes)
    return " ".join(parts)


__all__ = ["Execution", "ExecutionError", "OperationExecutor", "resolve_blocks"]
