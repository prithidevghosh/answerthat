"""The CSL style shortlist, and the marker family each style belongs to.

`packages/csl-styles/` is mounted into the api container **and read by the frontend's
citation.js**. One copy, or preview and export drift (HR-4). Nothing in this module may
render a citation itself; it only says where the files are.

The marker family is what style detection uses to narrow the candidate set before doing
the expensive round-trip scoring (ADR-011). `numeric` covers bracketed and superscript
numbering alike — Nature is superscript, IEEE is bracketed, but neither can be confused
with an author-date style by looking at an in-text marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.core.config import get_settings
from app.core.errors import StyleDetectionFailure

__all__ = ["MarkerFamily", "StyleInfo", "SHORTLIST", "style_path", "styles_for_family", "all_styles"]

MarkerFamily = Literal["numeric", "author_date"]


@dataclass(frozen=True)
class StyleInfo:
    style_id: str
    filename: str
    title: str
    family: MarkerFamily
    superscript: bool = False


# CP-3 fixes this list: APA 7, IEEE, ACM, Nature, Chicago author-date, Vancouver.
SHORTLIST: dict[str, StyleInfo] = {
    s.style_id: s
    for s in (
        StyleInfo("apa", "apa.csl", "APA Style 7th edition", "author_date"),
        StyleInfo("ieee", "ieee.csl", "IEEE Reference Guide", "numeric"),
        StyleInfo("acm", "acm-sig-proceedings.csl", "ACM SIG Proceedings", "numeric"),
        StyleInfo("nature", "nature.csl", "Nature", "numeric", superscript=True),
        StyleInfo(
            "chicago-author-date",
            "chicago-author-date.csl",
            "Chicago Manual of Style (author-date)",
            "author_date",
        ),
        StyleInfo(
            "vancouver",
            "vancouver.csl",
            "NLM/Vancouver: Citing Medicine (citation-sequence)",
            "numeric",
        ),
    )
}


def _styles_dir(override: Path | None = None) -> Path:
    return override if override is not None else get_settings().csl_styles_dir


def style_path(style_id: str, styles_dir: Path | None = None) -> Path:
    """Absolute path to a style's `.csl` file.

    Raises rather than falling back to a default style: rendering a paper in the wrong
    style silently is worse than refusing to render it.
    """
    info = SHORTLIST.get(style_id)
    if info is None:
        raise StyleDetectionFailure(
            f"unknown style_id {style_id!r}. Known styles: {', '.join(sorted(SHORTLIST))}"
        )
    path = _styles_dir(styles_dir) / info.filename
    if not path.is_file():
        raise StyleDetectionFailure(
            f"CSL file for {style_id!r} is missing at {path}. packages/csl-styles/ must be "
            "mounted into the container; the frontend reads the same directory (HR-4)."
        )
    return path


def styles_for_family(family: MarkerFamily) -> list[StyleInfo]:
    return [s for s in SHORTLIST.values() if s.family == family]


def all_styles() -> list[StyleInfo]:
    return list(SHORTLIST.values())
