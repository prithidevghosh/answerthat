"""CP-1: the IR → Pandoc → LaTeX round trip.

If these fail, nothing downstream is trustworthy — a review or an edit built on an IR
that cannot survive a render is built on sand. So the assertions here are deliberately
strict: paragraph count at ±0, section order exact, every anchor accounted for by ID.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.core.contracts import Document
from app.core.errors import ExportFailure, StyleDetectionFailure
from app.export.ast import citation_key_map, document_to_ast
from app.export.latex import build_bibliography, export_latex
from app.export.pandoc import pandoc_available, render_bibliography_entries
from app.export.roundtrip import verify_round_trip
from app.export.styles import SHORTLIST, style_path, styles_for_family
from app.ir.builder import DocumentBuilder
from app.ir.traversal import anchor_ids

REPO_ROOT = Path(__file__).resolve().parents[5]
STYLES_DIR = REPO_ROOT / "packages" / "csl-styles"

pytestmark = pytest.mark.skipif(not pandoc_available(), reason="pandoc is not installed")


def _unwrapped(latex: str) -> str:
    """Pandoc hard-wraps its LaTeX output, so a phrase can straddle a newline.

    Collapsing whitespace before asserting on content keeps these tests about fidelity
    rather than about where pandoc chose to break a line.
    """
    return " ".join(latex.split())


@pytest.fixture
def sources() -> dict[str, dict]:
    """CSL-JSON for the fixture document's sources. Read-only input to the exporter."""
    return {
        "src_vaswani": {
            "type": "paper-conference",
            "title": "Attention Is All You Need",
            "author": [{"family": "Vaswani", "given": "Ashish"}, {"family": "Shazeer", "given": "Noam"}],
            "issued": {"date-parts": [[2017]]},
            "container-title": "Advances in Neural Information Processing Systems",
            "page": "5998-6008",
        },
        "src_tay": {
            "type": "article-journal",
            "title": "Efficient Transformers: A Survey",
            "author": [{"family": "Tay", "given": "Yi"}],
            "issued": {"date-parts": [[2022]]},
            "container-title": "ACM Computing Surveys",
            "page": "1-28",
        },
        "src_child": {
            "type": "article-journal",
            "title": "Generating Long Sequences with Sparse Transformers",
            "author": [{"family": "Child", "given": "Rewon"}],
            "issued": {"date-parts": [[2019]]},
            "container-title": "arXiv",
        },
    }


# ---------------------------------------------------------------- styles


def test_all_six_shortlisted_styles_are_present_and_readable() -> None:
    """CP-3 fixes the shortlist. A missing file means export silently can't use it."""
    assert set(SHORTLIST) == {"apa", "ieee", "acm", "nature", "chicago-author-date", "vancouver"}
    for style_id in SHORTLIST:
        path = style_path(style_id, STYLES_DIR)
        assert path.is_file() and path.stat().st_size > 1000


def test_marker_families_partition_the_shortlist() -> None:
    numeric = {s.style_id for s in styles_for_family("numeric")}
    author_date = {s.style_id for s in styles_for_family("author_date")}
    assert numeric == {"ieee", "acm", "nature", "vancouver"}
    assert author_date == {"apa", "chicago-author-date"}
    assert numeric & author_date == set()


def test_unknown_style_raises_rather_than_defaulting() -> None:
    with pytest.raises(StyleDetectionFailure):
        style_path("harvard-imaginary", STYLES_DIR)


# ---------------------------------------------------------------- AST mapping


def test_citation_keys_are_unique_per_source() -> None:
    """Two sources colliding onto one key would silently merge two citations into one."""
    keys = citation_key_map(["10.1234/abc", "10.1234-abc", "10.1234/abc"])
    assert len(set(keys.values())) == 2
    assert keys["10.1234/abc"] != keys["10.1234-abc"]


def test_ast_anchor_becomes_identified_span(sample_doc: Document) -> None:
    """Every anchor must reach the AST as a Span carrying its own id — that identifier
    is what survives into the .tex and makes the round trip checkable."""
    keys = citation_key_map(["src_vaswani", "src_tay", "src_child"])
    ast = document_to_ast(sample_doc, keys)
    found = re.findall(r'"(anc_[0-9a-f]+)"', json.dumps(ast))
    assert sorted(found) == sorted(anchor_ids(sample_doc))


def test_ast_raises_when_a_cited_source_has_no_key(sample_doc: Document) -> None:
    """HR-5: a citation we cannot render is not quietly dropped."""
    with pytest.raises(ExportFailure, match="not in the supplied bibliography"):
        document_to_ast(sample_doc, citation_key_map(["src_vaswani"]))


def test_bibliography_covers_every_cited_source(sample_doc: Document, sources: dict) -> None:
    keys = citation_key_map(sorted(sources))
    entries = build_bibliography(sample_doc, sources, keys)
    assert {e["id"] for e in entries} == set(keys.values())


def test_bibliography_refuses_to_omit_a_missing_source(sample_doc: Document, sources: dict) -> None:
    del sources["src_tay"]
    keys = citation_key_map(sorted(sources) + ["src_tay"])
    with pytest.raises(ExportFailure, match="src_tay"):
        build_bibliography(sample_doc, sources, keys)


# ---------------------------------------------------------------- export


def test_export_refuses_without_a_style(sample_doc: Document, sources: dict) -> None:
    """HR-3: no default style. Rendering in the wrong style silently is a substitution."""
    with pytest.raises(ExportFailure, match="no citation style selected"):
        export_latex(sample_doc, sources, styles_dir=STYLES_DIR)


def test_the_render_probe_does_not_need_a_chosen_style(sample_doc: Document, sources: dict) -> None:
    """The kernel's rule 5 asks whether the document renders, not how it should look.

    ADR-011 leaves an ambiguous style to the user, so most documents carry `style_id=None`
    for a while. When the probe inherited export's refusal, every edit to such a document
    came back `pandoc_refused: no citation style selected` — a rejection of the *edit* for
    a choice nobody had been asked to make. Export's refusal above is unaffected.
    """
    from app.api.adapters import PandocRenderProbe

    assert sample_doc.metadata.style_id is None
    probe = PandocRenderProbe(lambda _doc: sources, STYLES_DIR)
    ok, reason = probe.can_render(sample_doc)
    assert ok, reason


def test_export_renders_through_pandoc(sample_doc: Document, sources: dict) -> None:
    result = export_latex(sample_doc, sources, style_id="ieee", styles_dir=STYLES_DIR)
    assert result.latex.startswith("%") or "\\documentclass" in result.latex
    assert "\\begin{document}" in result.latex
    assert result.style_id == "ieee"


def test_export_includes_a_citeproc_bibliography(sample_doc: Document, sources: dict) -> None:
    """HR-4: the reference list is citeproc's output, not ours."""
    latex = export_latex(sample_doc, sources, style_id="ieee", styles_dir=STYLES_DIR).latex
    assert "CSLReferences" in latex
    assert "Attention Is All You Need" in _unwrapped(latex)


def test_placeholders_are_visible_in_the_output(sample_doc: Document, sources: dict) -> None:
    """ADR-008: a stated scope cut the reader can see, carrying its caption."""
    latex = export_latex(sample_doc, sources, style_id="ieee", styles_dir=STYLES_DIR).latex
    assert "FIGURE NOT REPRODUCED" in _unwrapped(latex)
    assert "Throughput against sequence length" in _unwrapped(latex)


def test_latex_special_characters_are_escaped_not_injected(sources: dict) -> None:
    """Pandoc escapes when it serialises Str; we never build LaTeX by hand."""
    b = DocumentBuilder("doc_esc", title="Costs & Benefits: 50% of $x")
    section = b.section("Results \\& Discussion")
    section.paragraph("We measured a 95% reduction in $O(n^2)$ cost — see #3 {ok}.")
    latex = export_latex(b.build(), {}, style_id="apa", styles_dir=STYLES_DIR).latex
    assert "\\%" in latex and "\\$" in latex and "\\#" in latex
    # The raw, unescaped sequence must not appear as a live control sequence.
    assert "50% of" not in latex


@pytest.mark.parametrize("style_id", sorted(SHORTLIST))
def test_every_shortlisted_style_renders(style_id: str, sample_doc: Document, sources: dict) -> None:
    latex = export_latex(sample_doc, sources, style_id=style_id, styles_dir=STYLES_DIR).latex
    assert "\\begin{document}" in latex


# ---------------------------------------------------------------- CP-1 round trip


def test_round_trip_preserves_everything(sample_doc: Document, sources: dict) -> None:
    """The CP-1 criterion, in full: title, section order, paragraph count ±0, anchors."""
    report = verify_round_trip(sample_doc, sources, style_id="ieee", styles_dir=STYLES_DIR)
    assert report.failures() == []
    assert report.ok
    assert report.found_title == "Attention Considered Expensive"
    assert report.found_sections == ["Introduction", "Method"]
    assert report.found_paragraphs == report.expected_paragraphs == 3
    assert sorted(report.found_anchors) == sorted(report.expected_anchors)
    assert len(report.expected_anchors) == 3


@pytest.mark.parametrize("style_id", sorted(SHORTLIST))
def test_round_trip_holds_for_every_style(style_id: str, sample_doc: Document, sources: dict) -> None:
    """Fidelity must not depend on which style the paper happens to use."""
    report = verify_round_trip(sample_doc, sources, style_id=style_id, styles_dir=STYLES_DIR)
    assert report.failures() == [], f"{style_id}: {report.failures()}"


def test_round_trip_detects_a_lost_anchor(sample_doc: Document, sources: dict) -> None:
    """A round trip that cannot fail proves nothing — so prove it can."""
    report = verify_round_trip(sample_doc, sources, style_id="ieee", styles_dir=STYLES_DIR)
    report.expected_anchors = [*report.expected_anchors, "anc_deadbeef0000"]
    assert not report.ok
    assert report.missing_anchors == ["anc_deadbeef0000"]
    assert "anchors lost in export" in report.failures()[0]


def test_round_trip_detects_reordered_sections(sample_doc: Document, sources: dict) -> None:
    report = verify_round_trip(sample_doc, sources, style_id="ieee", styles_dir=STYLES_DIR)
    report.expected_sections = ["Method", "Introduction"]
    assert not report.sections_ok


def test_round_trip_survives_a_document_with_no_citations(sources: dict) -> None:
    b = DocumentBuilder("doc_bare", title="A Paper With No References")
    section = b.section("Introduction")
    section.paragraph("Nothing is cited here at all.")
    report = verify_round_trip(b.build(), {}, style_id="apa", styles_dir=STYLES_DIR)
    assert report.failures() == []


def test_round_trip_preserves_a_multi_level_document(sources: dict) -> None:
    """Subsections are sections too; order across levels must hold."""
    b = DocumentBuilder("doc_deep", title="Deep Structure")
    b.section("Introduction", level=1).paragraph("One.")
    b.section("Background", level=2).paragraph("Two.")
    b.section("Method", level=1).paragraph("Three.")
    report = verify_round_trip(b.build(), {}, style_id="apa", styles_dir=STYLES_DIR)
    assert report.found_sections == ["Introduction", "Background", "Method"]
    assert report.found_paragraphs == 3
    assert report.failures() == []


# ---------------------------------------------------------------- single-entry rendering


def test_render_bibliography_entries_returns_one_string_per_entry(sources: dict) -> None:
    """Style detection depends on this alignment; a skipped entry would misalign zip()."""
    entries = [{**csl, "id": key} for key, csl in sources.items()]
    rendered = render_bibliography_entries(entries, csl_path=style_path("apa", STYLES_DIR))
    assert len(rendered) == len(entries)
    assert all(r for r in rendered)
    assert "Vaswani" in rendered[0]


def test_rendered_entry_differs_by_style(sources: dict) -> None:
    """If two styles rendered identically, round-trip style scoring could not work."""
    entry = [{**sources["src_vaswani"], "id": "src_vaswani"}]
    apa = render_bibliography_entries(entry, csl_path=style_path("apa", STYLES_DIR))[0]
    ieee = render_bibliography_entries(entry, csl_path=style_path("ieee", STYLES_DIR))[0]
    assert apa != ieee
