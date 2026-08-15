"""The typed operation vocabulary (ADR-007, ADR-009).

`Operation` in Appendix A is deliberately loose — `params: dict` — so that the frozen
contract does not have to change every time an operation grows a knob. This module is
where that dict acquires meaning: each operation has a params model, and an `Operation`
that does not validate against it never reaches an executor.

That matters more than it looks. The planner is a language model emitting structured
output; `params` is the one place it could smuggle free text into the pipeline. Every
field below is a bounded scalar, an id, or an instruction string that is only ever handed
to the *text* model — never to anything that can mint a citation.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationError

from app.core.contracts import Operation, OperationType


class OperationSpecError(ValueError):
    """An operation whose params do not match its type. Surfaced, never swallowed (HR-3)."""


# --------------------------------------------------------------------------- params


class AddCitationsParams(BaseModel):
    """Find unsupported claims in the target, retrieve via B2, verify, insert anchors."""

    count: Annotated[int, Field(ge=1, le=20)] = 3
    criteria: str | None = None  # e.g. "recent work on sparse attention"


class FindSupportParams(BaseModel):
    """Retrieve + verify only. Proposes anchors; asserts no new text."""

    claim_ids: list[str] = Field(default_factory=list)
    count: Annotated[int, Field(ge=1, le=20)] = 3


class ShortenParams(BaseModel):
    """Detach → compress → reattach."""

    ratio: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.7  # target length as a fraction


class RewriteSectionParams(BaseModel):
    """Same discipline as Shorten, no length target."""

    instruction: Annotated[str, Field(min_length=1, max_length=2000)]


class ReplaceCitationParams(BaseModel):
    """Swap the source behind an anchor. Keeps the anchor and its position."""

    anchor_id: str
    new_source_id: str
    old_source_id: str | None = None  # None → the anchor's single existing source


class MoveTextParams(BaseModel):
    """Relocate blocks with their anchors still attached."""

    to_section_id: str
    after_block_id: str | None = None


class FreeformEditParams(BaseModel):
    """The escape hatch (ADR-009). Same kernel, generic invariants only."""

    instruction: Annotated[str, Field(min_length=1, max_length=2000)]


PARAMS_MODEL: dict[OperationType, type[BaseModel]] = {
    OperationType.ADD_CITATIONS: AddCitationsParams,
    OperationType.FIND_SUPPORT: FindSupportParams,
    OperationType.SHORTEN: ShortenParams,
    OperationType.REWRITE_SECTION: RewriteSectionParams,
    OperationType.REPLACE_CITATION: ReplaceCitationParams,
    OperationType.MOVE_TEXT: MoveTextParams,
    OperationType.FREEFORM_EDIT: FreeformEditParams,
}

#: Operations that rewrite prose and therefore run detach → transform → reattach (ADR-013).
TEXT_TRANSFORM_OPS = frozenset(
    {OperationType.SHORTEN, OperationType.REWRITE_SECTION, OperationType.FREEFORM_EDIT}
)

#: Operations that may introduce citations, and so must go through B2's retrieval.
CITATION_OPS = frozenset(
    {OperationType.ADD_CITATIONS, OperationType.FIND_SUPPORT, OperationType.REPLACE_CITATION}
)

TargetKind = Literal["section", "block", "span", "anchor"]


def parse_params(operation: Operation) -> BaseModel:
    """Validate `operation.params` against its operation type.

    Raises OperationSpecError with a message written for the planner to read — a rejected
    plan comes back with this text, and a vague message wastes one of two retries.
    """
    model = PARAMS_MODEL.get(operation.op)
    if model is None:
        raise OperationSpecError(f"unknown operation type {operation.op!r}")

    if not operation.target_ids:
        raise OperationSpecError(
            f"{operation.op.value} requires at least one target id (a section, block or span id "
            f"that exists in the document)"
        )

    try:
        parsed = model.model_validate(operation.params)
    except ValidationError as exc:
        raise OperationSpecError(
            f"{operation.op.value} params are invalid: {_compact(exc)}. "
            f"Expected fields: {sorted(model.model_fields)}"
        ) from exc

    _check_freeform_gate(operation)
    return parsed


def _check_freeform_gate(operation: Operation) -> None:
    """ADR-009: the hatch is gated, and the gate is checked here rather than trusted."""
    if operation.op is OperationType.FREEFORM_EDIT:
        if not operation.no_typed_op_applies:
            raise OperationSpecError(
                "FreeformEdit requires no_typed_op_applies=True. If a typed operation "
                "(AddCitations, FindSupport, Shorten, RewriteSection, ReplaceCitation, MoveText) "
                "can express this command, emit that instead."
            )
        if not (operation.justification or "").strip():
            raise OperationSpecError(
                "FreeformEdit requires a justification explaining why no typed operation applies."
            )
    elif operation.no_typed_op_applies:
        raise OperationSpecError(
            f"no_typed_op_applies is only meaningful on FreeformEdit, not {operation.op.value}"
        )


def _compact(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
    )


__all__ = [
    "AddCitationsParams",
    "CITATION_OPS",
    "FindSupportParams",
    "FreeformEditParams",
    "MoveTextParams",
    "OperationSpecError",
    "PARAMS_MODEL",
    "ReplaceCitationParams",
    "RewriteSectionParams",
    "ShortenParams",
    "TEXT_TRANSFORM_OPS",
    "parse_params",
]
