"""Style detection by round-trip scoring. ADR-011 / CP-3."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.contracts import ConfidenceTier, ParsedReference
from app.core.errors import StyleDetectionFailure
from app.export.pandoc import pandoc_available, render_bibliography_entries
from app.export.styles import style_path
from app.parsing.style import classify_marker_family, detect_style

REPO_ROOT = Path(__file__).resolve().parents[5]
STYLES_DIR = REPO_ROOT / "packages" / "csl-styles"

CSL_RECORDS = [
    {
        "type": "paper-conference",
        "title": "Attention Is All You Need",
        "author": [{"family": "Vaswani", "given": "Ashish"}],
        "issued": {"date-parts": [[2017]]},
        "container-title": "Advances in Neural Information Processing Systems",
    },
    {
        "type": "article-journal",
        "title": "Efficient Transformers: A Survey",
        "author": [{"family": "Tay", "given": "Yi"}],
        "issued": {"date-parts": [[2022]]},
        "container-title": "ACM Computing Surveys",
        "volume": "55",
        "page": "1-28",
    },
    {
        "type": "book",
        "title": "The Elements of Statistical Learning",
        "author": [{"family": "Hastie", "given": "Trevor"}],
        "issued": {"date-parts": [[2009]]},
        "publisher": "Springer",
    },
]


def _references(raw_strings: list[str]) -> list[ParsedReference]:
    return [
        ParsedReference(
            ref_id=f"b{index}",
            raw_string=raw,
            csl={**csl, "id": f"b{index}"},
            tier=ConfidenceTier.RESOLVED,
            parse_confidence=0.9,
        )
        for index, (csl, raw) in enumerate(zip(CSL_RECORDS, raw_strings, strict=True))
    ]


# ---------------------------------------------------------------- marker family


def test_numeric_markers_are_classified_numeric() -> None:
    verdict = classify_marker_family(["[1]", "[2, 3]", "(4)", "12", "[5-7]"])
    assert verdict.family == "numeric"
    assert verdict.confidence == 1.0


def test_author_date_markers_are_classified_author_date() -> None:
    verdict = classify_marker_family(
        ["(Vaswani, 2017)", "Tay et al. (2022)", "(Hastie & Tibshirani, 2009)"]
    )
    assert verdict.family == "author_date"


def test_a_mixed_bibliography_reports_the_majority_and_the_dissent() -> None:
    verdict = classify_marker_family(["[1]", "[2]", "[3]", "(Smith, 2020)"])
    assert verdict.family == "numeric"
    assert verdict.author_date == 1
    assert verdict.confidence == pytest.approx(0.75)


def test_an_even_split_refuses_to_pick() -> None:
    """Guessing here narrows the candidate set to the wrong half."""
    assert classify_marker_family(["[1]", "(Smith, 2020)"]).family is None


def test_no_markers_yields_no_family() -> None:
    verdict = classify_marker_family([])
    assert verdict.family is None
    assert verdict.confidence == 0.0


def test_unclassifiable_markers_are_counted_not_assigned() -> None:
    verdict = classify_marker_family(["[1]", "***", "†"])
    assert verdict.unclassifiable == 2
    assert verdict.numeric == 1


# ---------------------------------------------------------------- round-trip scoring

pytestmark = pytest.mark.skipif(not pandoc_available(), reason="pandoc is not installed")


def _rendered_as(style_id: str) -> list[str]:
    """Render our records through a style, to stand in for extracted raw strings."""
    entries = [{**csl, "id": f"b{index}"} for index, csl in enumerate(CSL_RECORDS)]
    return render_bibliography_entries(entries, csl_path=style_path(style_id, STYLES_DIR))


@pytest.mark.parametrize("style_id", ["ieee", "apa", "nature", "vancouver", "acm"])
def test_detection_recovers_the_style_its_strings_were_rendered_in(style_id: str) -> None:
    """The core of ADR-011: argmin over normalised Levenshtein against the raw strings."""
    markers = ["(Vaswani, 2017)"] if style_id == "apa" else ["[1]", "[2]"]
    result = detect_style(
        _references(_rendered_as(style_id)),
        markers,
        styles_dir=STYLES_DIR,
    )
    assert result.style_id == style_id, result.reason
    assert not result.ambiguous
    assert result.score is not None and result.score < 0.2


def test_the_score_is_exposed_and_explicable() -> None:
    """CP-3 requires the numeric score, not just the winner."""
    result = detect_style(_references(_rendered_as("ieee")), ["[1]"], styles_dir=STYLES_DIR)
    assert result.score is not None
    assert result.similarity == pytest.approx(1.0 - result.score)
    assert result.compared == 3
    assert len(result.candidates) >= 2
    assert result.candidates[0].distance <= result.candidates[1].distance
    assert result.candidates[0].per_reference and len(result.candidates[0].per_reference) == 3
    assert "Levenshtein" in result.reason


def test_marker_family_narrows_the_candidate_set() -> None:
    """Numeric markers must not leave APA on the shortlist."""
    result = detect_style(_references(_rendered_as("ieee")), ["[1]", "[2]"], styles_dir=STYLES_DIR)
    considered = {c.style_id for c in result.candidates}
    assert considered == {"ieee", "acm", "nature", "vancouver"}
    assert "apa" not in considered


def test_an_unclassifiable_marker_family_scores_the_full_shortlist() -> None:
    result = detect_style(_references(_rendered_as("apa")), [], styles_dir=STYLES_DIR)
    assert result.marker_family.family is None
    assert len(result.candidates) == 6


def test_top_two_within_the_margin_returns_ambiguous() -> None:
    """The user picks. We do not break the tie by preference or alphabetical order."""
    result = detect_style(
        _references(_rendered_as("ieee")),
        ["[1]"],
        styles_dir=STYLES_DIR,
        ambiguity_margin=1.0,  # forces the top two inside the margin
    )
    assert result.ambiguous
    assert result.style_id is None
    assert result.margin is not None
    assert "your choice to make" in result.reason
    # The evidence is still there — ambiguity is a report, not a shrug.
    assert result.candidates[0].distance <= result.candidates[1].distance


def test_realistic_ieee_strings_detect_ieee() -> None:
    """Not self-rendered: these are shaped like what GROBID actually extracts."""
    raw = [
        'A. Vaswani, "Attention is all you need," in Advances in Neural Information Processing Systems, 2017.',
        'Y. Tay, "Efficient transformers: A survey," ACM Computing Surveys, vol. 55, pp. 1-28, 2022.',
        "T. Hastie, The Elements of Statistical Learning. Springer, 2009.",
    ]
    result = detect_style(_references(raw), ["[1]", "[2]", "[3]"], styles_dir=STYLES_DIR)
    assert result.style_id == "ieee", result.reason


def test_realistic_apa_strings_detect_apa() -> None:
    raw = [
        "Vaswani, A. (2017). Attention Is All You Need. Advances in Neural Information Processing Systems.",
        "Tay, Y. (2022). Efficient Transformers: A Survey. ACM Computing Surveys, 55, 1-28.",
        "Hastie, T. (2009). The Elements of Statistical Learning. Springer.",
    ]
    result = detect_style(
        _references(raw), ["(Vaswani, 2017)", "(Tay, 2022)"], styles_dir=STYLES_DIR
    )
    assert result.style_id == "apa", result.reason


# ---------------------------------------------------------------- failure handling


def test_nothing_scorable_raises_rather_than_guessing() -> None:
    """A quarantined bibliography has nothing to render; that is a parse problem."""
    unusable = [
        ParsedReference(
            ref_id="b0",
            raw_string="mumble",
            csl=None,
            tier=ConfidenceTier.QUARANTINED,
            parse_confidence=0.0,
        )
    ]
    with pytest.raises(StyleDetectionFailure, match="nothing"):
        detect_style(unusable, ["[1]"], styles_dir=STYLES_DIR)


def test_references_without_raw_strings_are_excluded_and_reported() -> None:
    """`compared` must never overstate the evidence behind the score."""
    references = _references(_rendered_as("ieee"))
    references[2] = references[2].model_copy(update={"raw_string": ""})
    result = detect_style(references, ["[1]"], styles_dir=STYLES_DIR)
    assert result.compared == 2
