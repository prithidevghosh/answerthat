"""The orchestrator's prompts, as files (ADR-019).

Two kinds live here and the difference between them is the whole point of §0.

**The system prompt** establishes role, scope, honesty and standing behaviour. It is
policy — "when parsing completes, summarise the counts and *ask* before printing the
result" — not a script. It contains no sentence the researcher will ever read verbatim.

**The notice templates** carry facts and no instructions. When a background job changes
state the runtime fills one in and appends it to the conversation as a `system_notice`,
then runs a turn. The notice says forty-seven references resolved into four tiers; the
model writes the sentence. That split is what keeps this an agent rather than a state
machine with a chat skin: if a notice told the model what to say, the copy would be ours
and the agent would be a template engine.

A missing prompt file raises. There is no default and no empty-string fallback — a model
running with no instructions produces plausible output for the wrong task, which is the
hardest kind of failure to notice (HR-3).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent


def load(filename: str) -> str:
    path = _DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"prompt {filename!r} is missing from {_DIR}. Prompts are files in the owning "
            f"package (ADR-019); this one was expected and is not there."
        )
    return path.read_text(encoding="utf-8").strip()


SYSTEM_TEMPLATE = load("system.md")
_NOTICES_SOURCE = load("notices.md")


def _parse_notices(source: str) -> dict[str, str]:
    """Split `notices.md` on its `## name` headings.

    One file rather than five, because the notices are read together — the property that
    matters is that *none* of them instructs the model, and that is far easier to check
    when they sit on one screen.
    """
    blocks: dict[str, str] = {}
    for match in re.finditer(r"^##[ \t]+(\S+)[ \t]*$(.*?)(?=^##[ \t]|\Z)", source, re.M | re.S):
        blocks[match.group(1)] = match.group(2).strip()
    return blocks


NOTICES = _parse_notices(_NOTICES_SOURCE)

#: Every notice the runtime and the watcher can emit. Named here so a typo in a call is a
#: KeyError at import-adjacent time rather than a silently missing message.
NOTICE_NAMES = (
    "parse_complete",
    "parse_failed",
    "review_complete",
    "review_failed",
    "budget_exhausted",
)

_missing = [name for name in NOTICE_NAMES if name not in NOTICES]
if _missing:  # pragma: no cover - a packaging error, caught at import
    raise RuntimeError(
        f"notices.md is missing the block(s) {_missing}. A notice that cannot be rendered "
        "means a state transition the user is never told about, which is the silent "
        "failure HR-3 forbids."
    )


class _Blank(dict):
    """Renders an absent field as a visible placeholder rather than raising.

    A notice is built from whatever a job's status payload happens to carry, and a
    counter that a particular runner did not report should not take down the turn that
    was going to tell the user their parse finished. `<unknown>` is deliberately ugly: it
    is a fact about our reporting, and it should read as one rather than as a zero.
    """

    def __missing__(self, key: str) -> str:
        return "<unknown>"


def system_prompt(*, doc_id: str, title: str | None) -> str:
    """The system prompt for one conversation, naming the paper it is about."""
    return SYSTEM_TEMPLATE.format(
        doc_id=doc_id,
        title=title or "(not yet extracted — parsing may still be running)",
    )


def notice(name: str, **facts: Any) -> str:
    """Render one notice. Facts in, no instructions out.

    Whitespace is collapsed on the way out. The templates are wrapped at 90 columns
    because they live in a file people read, and those line breaks are a property of the
    file rather than of the fact — a notice whose sentences break in different places
    depending on how long a document id happens to be is a notice that reads as damaged.
    """
    if name not in NOTICES:
        raise KeyError(
            f"no notice template named {name!r}. Known: {', '.join(sorted(NOTICES))}"
        )
    rendered = NOTICES[name].format_map(_Blank(facts))
    return re.sub(r"\s+", " ", rendered).strip()


__all__ = [
    "NOTICES",
    "NOTICE_NAMES",
    "SYSTEM_TEMPLATE",
    "load",
    "notice",
    "system_prompt",
]
