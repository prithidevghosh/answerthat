"""Agreement scoring between our parse and an external record. ADR-001.

    agreement = 0.6·title_sim + 0.2·year_match + 0.2·first_author_sim
    accept at >= 0.85

Pure functions, no I/O, so the threshold behaviour is testable without a network. Two
notes on the parts that are underdetermined by the formula itself:

**Missing data scores zero, it does not get skipped.** If our parse has no year, the
year term contributes 0 rather than being dropped and the remaining weights renormalised.
Renormalising would mean a reference with only a title could reach 1.0 on a single fuzzy
field, which is how a confident mismatch gets accepted as canonical.

**A DOI match is identity, not similarity.** When both sides carry the same DOI they are
the same work by definition, and running a fuzzy title comparison on that is answering a
question that has already been answered exactly. That case scores 1.0 and is labelled
`doi_identity` so the audit view can show *why* it resolved. See memory.md §4.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from rapidfuzz import fuzz

__all__ = ["AgreementBreakdown", "score_agreement", "normalise_title", "TITLE_W", "YEAR_W", "AUTHOR_W"]

TITLE_W = 0.6
YEAR_W = 0.2
AUTHOR_W = 0.2

# A publication year that differs by one is overwhelmingly a preprint/version gap rather
# than a different paper, so it scores half rather than zero. Two years apart is not.
_NEAR_YEAR_CREDIT = 0.5

_NON_ALNUM = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class AgreementBreakdown:
    """The score and its components, so a user can see why something did or didn't match."""

    score: float
    title_sim: float
    year_match: float
    first_author_sim: float
    matched_on: Literal["formula", "doi_identity"] = "formula"

    our_title: str | None = None
    their_title: str | None = None
    our_year: int | None = None
    their_year: int | None = None
    our_first_author: str | None = None
    their_first_author: str | None = None

    def accepted(self, threshold: float) -> bool:
        return self.score >= threshold


def normalise_title(title: str | None) -> str:
    if not title:
        return ""
    folded = unicodedata.normalize("NFKD", title)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = _NON_ALNUM.sub(" ", folded.casefold())
    return _WS.sub(" ", folded).strip()


def _year_of(csl: dict[str, Any] | None) -> int | None:
    if not csl:
        return None
    issued = csl.get("issued") or {}
    parts = issued.get("date-parts") if isinstance(issued, dict) else None
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        try:
            return int(parts[0][0])
        except (TypeError, ValueError):
            return None
    return None


def _first_author_of(csl: dict[str, Any] | None) -> str | None:
    if not csl:
        return None
    authors = csl.get("author")
    if not isinstance(authors, list) or not authors:
        return None
    first = authors[0]
    if not isinstance(first, dict):
        return None
    family = str(first.get("family") or "").strip()
    if family:
        particle = str(first.get("non-dropping-particle") or "").strip()
        return f"{particle} {family}".strip()
    literal = str(first.get("literal") or "").strip()
    return literal or None


def _doi_of(csl: dict[str, Any] | None) -> str | None:
    if not csl:
        return None
    doi = csl.get("DOI") or csl.get("doi")
    return str(doi).strip().casefold().removeprefix("https://doi.org/") if doi else None


def _year_agreement(ours: int | None, theirs: int | None) -> float:
    if ours is None or theirs is None:
        return 0.0
    if ours == theirs:
        return 1.0
    return _NEAR_YEAR_CREDIT if abs(ours - theirs) == 1 else 0.0


def _string_similarity(ours: str, theirs: str) -> float:
    if not ours or not theirs:
        return 0.0
    return fuzz.ratio(ours, theirs) / 100.0


def score_agreement(ours: dict[str, Any] | None, theirs: dict[str, Any] | None) -> AgreementBreakdown:
    """Score an external record against our parse."""
    our_title = normalise_title((ours or {}).get("title"))
    their_title = normalise_title((theirs or {}).get("title"))
    our_year = _year_of(ours)
    their_year = _year_of(theirs)
    our_author = _first_author_of(ours)
    their_author = _first_author_of(theirs)

    title_sim = _string_similarity(our_title, their_title)
    year_match = _year_agreement(our_year, their_year)
    author_sim = _string_similarity(
        normalise_title(our_author), normalise_title(their_author)
    )

    our_doi, their_doi = _doi_of(ours), _doi_of(theirs)
    if our_doi and their_doi and our_doi == their_doi:
        return AgreementBreakdown(
            score=1.0,
            title_sim=title_sim,
            year_match=year_match,
            first_author_sim=author_sim,
            matched_on="doi_identity",
            our_title=our_title or None,
            their_title=their_title or None,
            our_year=our_year,
            their_year=their_year,
            our_first_author=our_author,
            their_first_author=their_author,
        )

    score = TITLE_W * title_sim + YEAR_W * year_match + AUTHOR_W * author_sim
    return AgreementBreakdown(
        score=round(score, 4),
        title_sim=round(title_sim, 4),
        year_match=year_match,
        first_author_sim=round(author_sim, 4),
        our_title=our_title or None,
        their_title=their_title or None,
        our_year=our_year,
        their_year=their_year,
        our_first_author=our_author,
        their_first_author=their_author,
    )
