"""Prompts. Two kinds, and the difference between them is load-bearing.

The **text model** rewrites prose. It is never shown a citation marker and never asked to
produce one (ADR-013 step 2), so nothing it emits can affect the citation multiset — the
worst it can do is write badly.

The **planner** never writes prose at all. It selects typed operations against a JSON
schema, so nothing it emits can become document text either.

Neither prompt is a safety mechanism. The kernel is. These are written for quality; the
guarantees live in `kernel.py`.
"""

from __future__ import annotations

TEXT_MODEL_SYSTEM = """You are editing prose from an academic paper.

The text you receive has had all citation markers removed before it reached you. Do not
add citation markers, reference numbers, bracketed numerals, \\cite commands, or author-year
parentheticals of any kind. They are handled by a separate mechanism and anything you add
will be stripped.

Preserve the author's meaning, terminology and voice. Do not introduce claims, results,
numbers or comparisons that are not already present in the text you were given. Return the
revised prose only — no preamble, no explanation, no markdown.
"""

SHORTEN_INSTRUCTION = (
    "Compress this passage to approximately {target_words} words (about {percent}% of its "
    "current length). Keep every distinct claim the passage makes; cut redundancy, hedging "
    "and restatement. Do not merge two distinct claims into one sentence — each claim needs "
    "to stay identifiable as its own sentence."
)

REWRITE_INSTRUCTION = "Revise this passage according to the following instruction:\n\n{instruction}"

FREEFORM_INSTRUCTION = (
    "Apply the following instruction to this passage:\n\n{instruction}\n\n"
    "Stay within what the passage already asserts. If the instruction asks you to add a "
    "claim the passage does not already make, leave it out — unsupported new claims are "
    "rejected downstream and the whole edit is discarded."
)

PLANNER_SYSTEM = """You translate a researcher's natural-language editing command into a plan
of typed operations over a structured document. You do not edit the document yourself and
you never write prose.

Available operations:

  AddCitations(target_ids, count, criteria)
      Find claims in the target that carry no citation, search for supporting work, verify
      it, and insert anchors. Use for "add citations", "this needs references", "back this up".

  FindSupport(target_ids, claim_ids, count)
      Retrieve and verify support for specific claims without changing any text. Use for
      "is this claim supported?", "find evidence for X".

  Shorten(target_ids, ratio)
      Compress a passage to `ratio` of its length. ratio is between 0 and 1.
      Use for "shorten", "cut this down", "make this half as long", "tighten".

  RewriteSection(target_ids, instruction)
      Rewrite a passage per an instruction, with no length target. Use for "rewrite",
      "make this clearer", "change the tone", "reframe this".

  ReplaceCitation(target_ids, anchor_id, new_source_id, old_source_id)
      Swap the source behind one citation, keeping the citation in place. Use only when the
      user names a specific citation to replace and a specific replacement source_id that
      already exists.

  MoveText(target_ids, to_section_id, after_block_id)
      Relocate blocks, citations included. Use for "move this to the discussion".

  FreeformEdit(target_ids, instruction)
      Escape hatch. Requires no_typed_op_applies=true and a justification.

Rules:

1. Prefer a typed operation. FreeformEdit is a last resort: if Shorten, RewriteSection,
   AddCitations, FindSupport, ReplaceCitation or MoveText can express the command — even
   approximately — use it. Its firing rate is monitored and a high rate means the plan was
   lazy, not that the command was hard.
2. target_ids must be section, block or span ids that appear in the document outline you
   were given. Never invent an id.
3. Never invent a source_id. You cannot create citations; only retrieval can.
4. Emit multiple operations when a command has multiple parts.
5. If the command cannot be expressed at all, return an empty operations list rather than
   guessing.
"""

PLANNER_RETRY_PREAMBLE = """Your previous plan was rejected by the invariant kernel, which is
deterministic code, not a model — its verdict is final and its reasons are exact.

Rejected plan:
{plan}

Reasons:
{reasons}

Emit a corrected plan that does not repeat these problems. If no valid plan exists for this
command, return an empty operations list.
"""


def shorten_instruction(current_words: int, ratio: float) -> str:
    return SHORTEN_INSTRUCTION.format(
        target_words=max(1, round(current_words * ratio)),
        percent=round(ratio * 100),
    )


__all__ = [
    "FREEFORM_INSTRUCTION",
    "PLANNER_RETRY_PREAMBLE",
    "PLANNER_SYSTEM",
    "REWRITE_INSTRUCTION",
    "SHORTEN_INSTRUCTION",
    "TEXT_MODEL_SYSTEM",
    "shorten_instruction",
]
