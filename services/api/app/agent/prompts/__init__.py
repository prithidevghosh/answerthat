"""B3's prompts, as files (ADR-019).

Files rather than inline string literals, and inside this package rather than in a shared
one. A prompt change is a behaviour change, and it should read like one in the history: a
diff on `planner_system.md` says what happened, where the same edit buried in a triple-
quoted string three levels into `planner.py` does not.

Two kinds live here, and the difference between them is load-bearing:

The **text model** rewrites prose. It is never shown a citation marker and never asked to
produce one (ADR-013 step 2), so nothing it emits can affect the citation multiset — the
worst it can do is write badly.

The **planner** never writes prose at all. It selects typed operations against a JSON
schema, so nothing it emits can become document text either.

Neither prompt is a safety mechanism. The kernel is. These are written for quality; the
guarantees live in `kernel.py`.
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent


def load(filename: str) -> str:
    """Read one prompt file.

    A missing file raises. There is no default prompt and no empty-string fallback: a
    model silently running with no instructions would produce plausible output for the
    wrong task, which is the hardest kind of failure to notice (HR-3).
    """
    path = _DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"prompt {filename!r} is missing from {_DIR}. Prompts are files in the owning "
            f"package (ADR-019); this one was expected and is not there."
        )
    return path.read_text(encoding="utf-8").strip()


TEXT_MODEL_SYSTEM = load("text_model_system.md")
SHORTEN_INSTRUCTION = load("shorten.md")
REWRITE_INSTRUCTION = load("rewrite.md")
FREEFORM_INSTRUCTION = load("freeform.md")
PLANNER_SYSTEM = load("planner_system.md")
PLANNER_RETRY_PREAMBLE = load("planner_retry.md")


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
    "load",
    "shorten_instruction",
]
