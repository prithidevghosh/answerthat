"""B2's prompts, as files (ADR-019).

Files rather than inline string literals, and inside this package rather than in a shared
one. A prompt change is a behaviour change and should read like one in the history: a diff
on `verify.md` says what happened, where the same edit buried in a triple-quoted string
halfway down `verifier.py` does not.

Three prompts, one per model role (ADR-015):

`claim_extraction.md` (role `CLAIM_EXTRACTION`) decomposes prose into atomic claims.
It returns offsets, not text — the claim is sliced out of the span in code, so the model
cannot paraphrase the author.

`rerank.md` (role `RERANK`) triages candidates against the claim. Highest call
volume in the system, which is why it sees only what survived the embedding prefilter.

`verify.md` (role `VERIFY`) decides entailment and must quote the abstract verbatim.

**None of these is a safety mechanism.** The offset check in `claims.py` and the substring
check in `verifier.py` are. These are written for quality; the guarantees are mechanical
and no prompt edit can weaken or strengthen them.
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent

__all__ = ["CLAIM_EXTRACTION_SYSTEM", "RERANK_SYSTEM", "VERIFIER_SYSTEM", "load"]


def load(filename: str) -> str:
    """Read one prompt file.

    A missing file raises. There is no default prompt and no empty-string fallback: a
    model running with no instructions still answers, and it answers plausibly for the
    wrong task — the hardest kind of failure to notice (HR-3).
    """
    path = _DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"prompt {filename!r} is missing from {_DIR}. Prompts are files in the owning "
            f"package (ADR-019); this one was expected and is not there."
        )
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(
            f"prompt {path} is empty. An empty system prompt is not a neutral one — it "
            "leaves the model to infer the task from the payload."
        )
    return text


CLAIM_EXTRACTION_SYSTEM = load("claim_extraction.md")
RERANK_SYSTEM = load("rerank.md")
VERIFIER_SYSTEM = load("verify.md")
