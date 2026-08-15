"""Style detection by round-trip scoring. ADR-011.

    classify the marker family (numeric vs author-date) to narrow the candidates
    → render our reconciled CSL-JSON through each shortlisted .csl via Pandoc
    → compare each rendering to the raw reference strings we actually extracted,
      by normalised Levenshtein
    → take the argmin, and expose the score
    → top two within 0.05 → ambiguous, the user picks

Deterministic and explainable, as against asking a model and getting an unverifiable
answer. It degrades into "we're not sure, you pick" exactly when it should. It also
doubles as a regression test for the whole parse pipeline: mean round-trip similarity
drops before anything else visibly breaks.

Two implementation notes:

**Entries are rendered one at a time, not as a batch.** A batch is six times fewer
subprocess calls, but citeproc *sorts* the bibliography — APA alphabetically, IEEE by
citation order — so the nth rendered entry is not the nth reference, and aligning them
by position would silently compare the wrong pairs. Correct alignment is worth the
subprocess calls, and the sample cap keeps the cost bounded.

**Leading entry labels are stripped from both sides before comparison.** Whether GROBID
captured `[1]` at the head of the raw string is an artefact of the PDF, not evidence
about the style. Numbering is already covered, far more reliably, by the marker family.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from app.core.contracts import ParsedReference
from app.core.errors import StyleDetectionFailure
from app.export.pandoc import render_bibliography_entries
from app.export.styles import MarkerFamily, StyleInfo, all_styles, style_path, styles_for_family

__all__ = [
    "MarkerFamilyVerdict",
    "StyleScore",
    "StyleDetectionResult",
    "classify_marker_family",
    "detect_style",
    "DEFAULT_SAMPLE_SIZE",
]

# Style is uniform across a bibliography, so a sample settles it. Twelve entries is
# plenty of signal and keeps detection to a few seconds rather than a minute.
DEFAULT_SAMPLE_SIZE = 12

_WS = re.compile(r"\s+")
# "[1] ", "1. ", "(1) " at the head of an entry.
_LEADING_LABEL = re.compile(r"^\s*(?:\[\s*\d+\s*\]|\(\s*\d+\s*\)|\d+\s*[.)])\s*")
# A marker made only of digits and separators: [1], [1,2], (3), 12, 1-3.
_NUMERIC_MARKER = re.compile(r"^[\s\[\(]*\d+(?:\s*[-–,;]\s*\d+)*[\s\]\)]*$")
_HAS_LETTERS = re.compile(r"[^\W\d_]", flags=re.UNICODE)


@dataclass(frozen=True)
class MarkerFamilyVerdict:
    """What the in-text markers say about the style family."""

    family: MarkerFamily | None
    numeric: int
    author_date: int
    unclassifiable: int
    confidence: float

    @property
    def total(self) -> int:
        return self.numeric + self.author_date + self.unclassifiable


@dataclass(frozen=True)
class StyleScore:
    """One candidate style's fit against the raw strings we extracted."""

    style_id: str
    title: str
    # Mean normalised Levenshtein distance. Lower is better; this is the argmin target.
    distance: float
    similarity: float
    compared: int
    per_reference: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class StyleDetectionResult:
    style_id: str | None
    score: float | None
    similarity: float | None
    ambiguous: bool
    marker_family: MarkerFamilyVerdict
    candidates: list[StyleScore]
    compared: int
    margin: float | None = None
    reason: str = ""

    @property
    def runner_up(self) -> StyleScore | None:
        return self.candidates[1] if len(self.candidates) > 1 else None


def _normalise(text: str) -> str:
    """Casefold and collapse whitespace. Punctuation is kept — it is the signal."""
    return _WS.sub(" ", _LEADING_LABEL.sub("", text)).strip().casefold()


def classify_marker_family(markers: list[str]) -> MarkerFamilyVerdict:
    """Numeric vs author-date, from the in-text markers themselves.

    A marker containing letters is an author name; one made only of digits and
    separators is a number. Markers we cannot classify are counted and reported rather
    than assigned to whichever side is winning.
    """
    numeric = author_date = unclassifiable = 0
    for marker in markers:
        text = (marker or "").strip()
        if not text:
            unclassifiable += 1
        elif _NUMERIC_MARKER.match(text):
            numeric += 1
        elif _HAS_LETTERS.search(text):
            author_date += 1
        else:
            unclassifiable += 1

    total = numeric + author_date + unclassifiable
    if total == 0 or numeric == author_date:
        return MarkerFamilyVerdict(None, numeric, author_date, unclassifiable, 0.0)

    family: MarkerFamily = "numeric" if numeric > author_date else "author_date"
    winning = max(numeric, author_date)
    return MarkerFamilyVerdict(family, numeric, author_date, unclassifiable, round(winning / total, 4))


def _scorable(references: list[ParsedReference], sample_size: int) -> list[ParsedReference]:
    """References that can be scored: they have both a CSL record and a raw string.

    A quarantined entry has nothing to render, and an entry with no raw string has
    nothing to compare against. Both are excluded, and `compared` reports how many
    actually contributed so the score is never read as covering more than it does.
    """
    usable = [r for r in references if r.csl and r.raw_string.strip()]
    return usable[:sample_size]


def _score_style(
    style: StyleInfo,
    sample: list[ParsedReference],
    styles_dir: Path | None,
) -> StyleScore:
    entries = [{**(r.csl or {}), "id": r.ref_id} for r in sample]
    rendered = render_bibliography_entries(entries, csl_path=style_path(style.style_id, styles_dir))

    distances: list[float] = []
    for reference, rendering in zip(sample, rendered, strict=True):
        distances.append(
            Levenshtein.normalized_distance(_normalise(rendering), _normalise(reference.raw_string))
        )

    mean = sum(distances) / len(distances)
    return StyleScore(
        style_id=style.style_id,
        title=style.title,
        distance=round(mean, 4),
        similarity=round(1.0 - mean, 4),
        compared=len(distances),
        per_reference=[round(d, 4) for d in distances],
    )


def detect_style(
    references: list[ParsedReference],
    markers: list[str],
    *,
    styles_dir: Path | None = None,
    ambiguity_margin: float = 0.05,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> StyleDetectionResult:
    """Detect the paper's citation style, or report honestly that we cannot.

    Raises `StyleDetectionFailure` only when scoring cannot run at all — no renderable
    references, or unreadable style files. An ambiguous or weak result is a legitimate
    outcome carried in the return value, not an exception.
    """
    verdict = classify_marker_family(markers)
    sample = _scorable(references, sample_size)

    if not sample:
        raise StyleDetectionFailure(
            "no reference has both a parsed record and a raw string, so there is nothing "
            "to score a style against. This is a parsing problem, not a style problem: "
            "check that GROBID was called with includeRawCitations=1."
        )

    # Narrowing by marker family is the cheap half of ADR-011. When the markers do not
    # agree with themselves we score the full shortlist rather than guessing.
    candidates = styles_for_family(verdict.family) if verdict.family else all_styles()

    scores = sorted(
        (_score_style(style, sample, styles_dir) for style in candidates),
        key=lambda s: s.distance,
    )

    best = scores[0]
    runner_up = scores[1] if len(scores) > 1 else None
    margin = round(runner_up.distance - best.distance, 4) if runner_up else None

    if margin is not None and margin < ambiguity_margin:
        return StyleDetectionResult(
            style_id=None,
            score=best.distance,
            similarity=best.similarity,
            ambiguous=True,
            marker_family=verdict,
            candidates=scores,
            compared=best.compared,
            margin=margin,
            reason=(
                f"{best.style_id} ({best.distance:.3f}) and {runner_up.style_id} "
                f"({runner_up.distance:.3f}) are within {ambiguity_margin} of each other. "
                "The bibliography does not distinguish them, so this is your choice to make."
            ),
        )

    return StyleDetectionResult(
        style_id=best.style_id,
        score=best.distance,
        similarity=best.similarity,
        ambiguous=False,
        marker_family=verdict,
        candidates=scores,
        compared=best.compared,
        margin=margin,
        reason=(
            f"{best.style_id} matched {best.compared} extracted reference string(s) with a mean "
            f"normalised Levenshtein distance of {best.distance:.3f}"
            + (f", ahead of {runner_up.style_id} by {margin:.3f}" if runner_up else "")
        ),
    )
