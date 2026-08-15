"""The planner: natural language in, typed operations out.

**Structured output only.** The model is handed a JSON schema whose `operations` are the
seven types in Appendix A and nothing else. There is no field on that schema into which
prose, a text edit, or a `source_id` can be written — so the failure mode where "the agent
rewrote my paragraph and dropped a citation" is not a bug to be fixed here, it is a shape
the planner's output cannot take.

Everything the planner emits is still validated by pure code before an executor sees it,
and everything an executor produces is still judged by the kernel. The planner is the
least trusted component in this package, and it is built that way on purpose.
"""

from __future__ import annotations

import json
import logging
import uuid

from pydantic import ValidationError

from app.agent import prompts
from app.agent.operations import OperationSpecError, parse_params
from app.agent.ports import StructuredModel
from app.core.contracts import Document, EditPlan, Operation, OperationType

log = logging.getLogger("app.agent.planner")

MAX_OUTLINE_CHARS = 240


class PlanningError(RuntimeError):
    """The planner produced something that is not a plan. Surfaced with its reason (HR-3)."""


# --------------------------------------------------------------------------- schema


def edit_plan_schema() -> dict:
    """The only shape the planner may return. Note what is absent: no free text field,
    no `text`, no `source_id`, no `csl`."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["operations"],
        "properties": {
            "operations": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["op", "target_ids"],
                    "properties": {
                        "op": {"type": "string", "enum": [t.value for t in OperationType]},
                        "target_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": "section, block or span ids taken from the outline",
                        },
                        "params": {
                            "type": "object",
                            "description": "operation-specific parameters; see the system prompt",
                        },
                        "no_typed_op_applies": {
                            "type": "boolean",
                            "description": "required true for FreeformEdit, false otherwise",
                        },
                        "justification": {
                            "type": "string",
                            "description": "required for FreeformEdit: why no typed operation fits",
                        },
                    },
                },
            }
        },
    }


def document_outline(document: Document) -> str:
    """The ids the planner is allowed to name, plus enough text to choose between them.

    Anchors are listed by id and marker, never as inline markers in the text — the planner
    has no business seeing prose with citations threaded through it either.
    """
    lines: list[str] = [f'document {document.doc_id} v{document.version}: "{document.metadata.title}"']
    for section in sorted(document.sections, key=lambda s: s.order):
        lines.append(f"  section {section.id} (h{section.level}) — {section.title}")
        for block in sorted(section.blocks, key=lambda b: b.order):
            lines.append(f"    block {block.id} [{block.type}]")
            for span in block.spans:
                text = span.text if len(span.text) <= MAX_OUTLINE_CHARS else span.text[:MAX_OUTLINE_CHARS] + "…"
                anchors = ", ".join(a.anchor_id for a in span.citation_anchors)
                suffix = f"  (anchors: {anchors})" if anchors else "  (no citation)"
                lines.append(f"      span {span.id}: {text}{suffix}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- planner


class Planner:
    def __init__(self, model: StructuredModel) -> None:
        self._model = model

    async def plan(
        self,
        *,
        command: str,
        document: Document,
        rejection_feedback: list[str] | None = None,
        previous_plan: EditPlan | None = None,
    ) -> EditPlan:
        prompt = self._build_prompt(command, document, rejection_feedback, previous_plan)
        raw = await self._model.plan(
            system=prompts.PLANNER_SYSTEM, prompt=prompt, schema=edit_plan_schema()
        )
        return self._materialize(raw)

    @staticmethod
    def _build_prompt(
        command: str,
        document: Document,
        rejection_feedback: list[str] | None,
        previous_plan: EditPlan | None,
    ) -> str:
        parts = [
            "Document outline (these are the only ids that exist):",
            document_outline(document),
            "",
            f"Command from the researcher:\n{command}",
        ]
        if rejection_feedback:
            parts += [
                "",
                prompts.PLANNER_RETRY_PREAMBLE.format(
                    plan=(
                        json.dumps([o.model_dump(mode="json") for o in previous_plan.operations], indent=2)
                        if previous_plan
                        else "(none)"
                    ),
                    reasons="\n".join(f"- {reason}" for reason in rejection_feedback),
                ),
            ]
        return "\n".join(parts)

    @staticmethod
    def _materialize(raw: dict) -> EditPlan:
        if not isinstance(raw, dict) or "operations" not in raw:
            raise PlanningError(
                f"planner returned {type(raw).__name__} without an 'operations' key; "
                f"structured output is required"
            )
        try:
            operations = [Operation.model_validate(item) for item in raw["operations"]]
        except (ValidationError, TypeError) as exc:
            raise PlanningError(f"planner emitted an operation that is not an Operation: {exc}") from exc

        plan = EditPlan(plan_id=raw.get("plan_id") or f"plan-{uuid.uuid4().hex[:12]}", operations=operations)
        log.info("plan %s: %s", plan.plan_id, [o.op.value for o in plan.operations])
        return plan


def validate_plan(plan: EditPlan, document: Document) -> list[str]:
    """Pure pre-flight on a plan. Anything caught here costs no model call and no executor
    work, and its message goes straight back to the planner as retry feedback."""
    problems: list[str] = []
    known_ids = {s.id for s in document.sections}
    for section in document.sections:
        for block in section.blocks:
            known_ids.add(block.id)
            known_ids.update(span.id for span in block.spans)

    if not plan.operations:
        problems.append(
            "the plan contains no operations. If the command cannot be expressed with the typed "
            "vocabulary, say so rather than returning an empty plan."
        )

    for index, operation in enumerate(plan.operations):
        unknown = [t for t in operation.target_ids if t not in known_ids]
        if unknown:
            problems.append(
                f"operation {index} ({operation.op.value}) targets {unknown}, which are not ids in "
                f"this document. Use ids from the outline."
            )
        try:
            parse_params(operation)
        except OperationSpecError as exc:
            problems.append(f"operation {index}: {exc}")

    return problems


__all__ = ["Planner", "PlanningError", "document_outline", "edit_plan_schema", "validate_plan"]
