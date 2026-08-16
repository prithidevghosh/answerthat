"""The constrained repair tier. ADR-003.

The model may only **segment and label the literal characters present** in the raw
reference string. It may not normalise, expand an abbreviation, correct a typo, or
supply a field the string does not contain.

Then the mechanical post-check: every emitted field value must be a substring of the raw
reference string after whitespace and punctuation normalisation. Anything else is
discarded and the entry is marked unparsed.

**This check is not optional and cannot be replaced by a better prompt.** It is the only
thing standing between "a parser" and "a fabrication engine wearing a parser's clothes",
and it works precisely because it does not care what the prompt said, what the model
intended, or how confident anything is. If you are here to relax it because it rejected
a technically-correct expansion of `Proc.` to `Proceedings`, the answer is no: that
expansion is a fact about the world that the model supplied, not a fact about the string
in front of it, and the mechanism cannot tell that apart from an invented author.

The tier runs **only** on entries below the confidence threshold. Running it on a
well-parsed entry risks replacing a good parse with a worse one for no gain.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.contracts import ConfidenceTier, ParsedReference
from app.core.errors import MissingAPIKeyError
from app.parsing.csl import parse_confidence_for

__all__ = [
    "ReferenceSegmenter",
    "RepairOutcome",
    "Violation",
    "normalise_for_containment",
    "check_substring_containment",
    "repair_reference",
    "repair_references",
    "REPAIRABLE_FIELDS",
]

# Fields the segmenter is allowed to emit. Anything else is dropped before the substring
# check even runs — an unexpected key is a sign the model is answering a different
# question than the one we asked.
REPAIRABLE_FIELDS = frozenset(
    {"title", "container-title", "author", "issued", "page", "volume", "issue", "publisher", "DOI", "type"}
)

# `type` is a CSL vocabulary term, not text from the reference, so it is exempt from
# containment — but it is validated against the vocabulary instead.
_EXEMPT_FROM_CONTAINMENT = frozenset({"type"})

_ALLOWED_TYPES = frozenset(
    {
        "article-journal", "paper-conference", "chapter", "book", "thesis",
        "report", "webpage", "document", "manuscript", "article",
    }
)

_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


class ReferenceSegmenter(Protocol):
    """Segments a raw reference string into labelled fields.

    Implementations must be told, in the strongest terms the prompt allows, to copy
    characters rather than produce them. That instruction is a courtesy to the model,
    not a guarantee to us — the guarantee is `check_substring_containment`.
    """

    async def segment(self, raw_string: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Violation:
    """A field value the model produced that is not present in the raw string."""

    path: str
    value: str
    reason: str = "not a substring of the raw reference string"

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.path}={self.value!r}: {self.reason}"


@dataclass
class RepairOutcome:
    ref_id: str
    accepted: bool
    csl: dict[str, Any] | None
    parse_confidence: float
    violations: list[Violation] = field(default_factory=list)
    ran: bool = True
    skipped_reason: str = ""


def normalise_for_containment(text: str) -> str:
    """Fold away the differences that are not evidence of invention.

    Case, unicode form, punctuation and whitespace vary freely between a reference
    string and any sane segmentation of it. What must not vary is the letters and digits
    and their order — that is the part a model cannot change without making something up.
    """
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.casefold()
    folded = _PUNCTUATION.sub(" ", folded)
    return _WHITESPACE.sub(" ", folded).strip()


def _leaf_strings(value: Any, path: str) -> list[tuple[str, str]]:
    """Every scalar leaf in a CSL value, with a dotted path for the error message."""
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, (int, float)):
        return [(path, str(value))]
    if isinstance(value, dict):
        return [pair for key, sub in value.items() for pair in _leaf_strings(sub, f"{path}.{key}")]
    if isinstance(value, list):
        return [pair for index, sub in enumerate(value) for pair in _leaf_strings(sub, f"{path}[{index}]")]
    return []


def check_substring_containment(csl: dict[str, Any], raw_string: str) -> list[Violation]:
    """Every emitted value must appear in the raw string. ADR-003's mechanical check.

    Returns the violations; an empty list means every character the model emitted was
    already in front of it.
    """
    haystack = normalise_for_containment(raw_string)
    violations: list[Violation] = []

    for key, value in csl.items():
        if key in _EXEMPT_FROM_CONTAINMENT:
            if key == "type" and value not in _ALLOWED_TYPES:
                violations.append(
                    Violation(path=key, value=str(value), reason="not a CSL type from the allowed vocabulary")
                )
            continue
        for path, leaf in _leaf_strings(value, key):
            needle = normalise_for_containment(leaf)
            if not needle:
                continue
            if needle not in haystack:
                violations.append(Violation(path=path, value=leaf))
    return violations


async def repair_reference(
    reference: ParsedReference,
    segmenter: ReferenceSegmenter,
    *,
    threshold: float,
) -> RepairOutcome:
    """Repair one reference, if it qualifies.

    A qualifying entry is below the confidence threshold and has a raw string to work
    from. Without a raw string there is nothing to check the output against, so there is
    no safe way to run this at all — and running it unchecked is exactly what ADR-003
    forbids.
    """
    if reference.parse_confidence >= threshold:
        return RepairOutcome(
            ref_id=reference.ref_id,
            accepted=False,
            csl=reference.csl,
            parse_confidence=reference.parse_confidence,
            ran=False,
            skipped_reason=f"confidence {reference.parse_confidence:.2f} is at or above threshold {threshold:.2f}",
        )

    if not reference.raw_string.strip():
        return RepairOutcome(
            ref_id=reference.ref_id,
            accepted=False,
            csl=reference.csl,
            parse_confidence=reference.parse_confidence,
            ran=False,
            skipped_reason=(
                "no raw reference string to check the output against; without one the "
                "substring check cannot run and an unchecked repair is not permitted (ADR-003)"
            ),
        )

    emitted = await segmenter.segment(reference.raw_string)

    # Drop unexpected keys before checking. They are not evidence of fabrication on
    # their own, but we did not ask for them and will not store them.
    candidate = {k: v for k, v in emitted.items() if k in REPAIRABLE_FIELDS}

    violations = check_substring_containment(candidate, reference.raw_string)
    if violations:
        # Strict reading of ADR-003: the offending values are discarded *and* the entry
        # is marked unparsed. A model that invented one field has demonstrated it will
        # invent, and there is no principled way to trust the rest of the same output.
        return RepairOutcome(
            ref_id=reference.ref_id,
            accepted=False,
            csl=None,
            parse_confidence=reference.parse_confidence,
            violations=violations,
        )

    if not candidate.get("title") and not candidate.get("DOI"):
        return RepairOutcome(
            ref_id=reference.ref_id,
            accepted=False,
            csl=None,
            parse_confidence=reference.parse_confidence,
            skipped_reason="segmentation produced neither a title nor a DOI",
        )

    candidate.setdefault("id", reference.ref_id)
    candidate.setdefault("note", reference.raw_string)
    return RepairOutcome(
        ref_id=reference.ref_id,
        accepted=True,
        csl=candidate,
        parse_confidence=parse_confidence_for(candidate),
    )


async def repair_references(
    references: list[ParsedReference],
    segmenter: ReferenceSegmenter,
    *,
    threshold: float,
) -> tuple[list[ParsedReference], list[RepairOutcome]]:
    """Run the repair tier across a bibliography.

    Returns the updated references and every outcome, including the skips. The skips
    matter: "the repair tier did not run on this entry" and "the repair tier ran and
    failed" are different things to show a user, and telemetry needs to tell them apart
    (ADR-002's revisit condition depends on it).
    """
    outcomes: list[RepairOutcome] = []
    updated: list[ParsedReference] = []

    for reference in references:
        outcome = await repair_reference(reference, segmenter, threshold=threshold)
        outcomes.append(outcome)

        if not outcome.ran:
            updated.append(reference)
            continue

        if outcome.accepted and outcome.csl:
            updated.append(
                reference.model_copy(
                    update={
                        "csl": outcome.csl,
                        "parse_confidence": outcome.parse_confidence,
                        "tier": (
                            ConfidenceTier.PARSED_UNRESOLVED
                            if outcome.parse_confidence >= threshold
                            else ConfidenceTier.LOW_CONFIDENCE
                        ),
                    }
                )
            )
            continue

        # Not accepted: the raw string is retained verbatim and the entry is honest
        # about being unparsed. It is never dropped (HR-3).
        updated.append(
            reference.model_copy(update={"csl": None, "tier": ConfidenceTier.QUARANTINED})
        )

    return updated, outcomes


def require_model_credentials(api_key: str | None) -> str:
    """The repair tier needs a model. Absent one, it raises rather than skipping.

    `OPENAI_API_KEY` is startup-fatal under HR-2 / ADR-015, so in a correctly configured
    process this never fires. It stays anyway, at the point of use: the failure it
    guards against is a repair tier that quietly does nothing, and a bibliography whose
    low-confidence entries were never repaired looks exactly like a bibliography full of
    genuinely bad references. One is a missing key and the other is a bad paper, and a
    silent skip makes them indistinguishable (HR-3).
    """
    key = (api_key or "").strip()
    if not key:
        raise MissingAPIKeyError(
            "OPENAI_API_KEY is not set, so the constrained repair tier cannot run "
            "(ADR-015 — every LLM role, this one included, goes through app/core/llm.py). "
            "Low-confidence references would silently stay unrepaired, which reads to a "
            "user like a paper full of bad references rather than a missing key. Set it "
            "in .env, or disable the repair tier explicitly."
        )
    return key
