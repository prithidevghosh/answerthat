"""Prompts belonging to the parsing package. ADR-019.

Prompts live in files, not in string literals, and they live *here* rather than in a
shared directory. Two reasons, both from ADR-019: a shared `prompts/` is a
shared-ownership directory across four parallel agents, and a prompt change is a
behaviour change — it should read like one in a diff, next to the code whose behaviour
it defines.

`app/core/llm.py` holds the client and the structured-output plumbing. It holds no
prompt text, and this module holds no model IDs.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["PROMPTS_DIR", "PromptMissing", "load_prompt"]

PROMPTS_DIR = Path(__file__).resolve().parent


class PromptMissing(RuntimeError):
    """A prompt file the code asked for is not on disk.

    Fatal by design. A pipeline stage that quietly ran with an empty prompt would
    produce plausible-looking output from no instruction at all, and for the repair
    tier that means a model free-associating over a reference string (HR-3).
    """


def load_prompt(name: str) -> str:
    """Read one prompt file by stem-and-suffix, e.g. `reference_repair.system.md`.

    Cached nowhere on purpose: these are read once at client construction, and a stale
    in-process copy of a prompt is a confusing thing to debug.
    """
    path = PROMPTS_DIR / name
    if not path.is_file():
        raise PromptMissing(
            f"prompt {name!r} is not in {PROMPTS_DIR}. Prompts are versioned files, not "
            "inline strings (ADR-019); if you renamed one, rename it here too rather than "
            "falling back to a default, which would run the model on no instruction."
        )
    return path.read_text(encoding="utf-8")
