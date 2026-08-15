"""CP-1's proof: does the exported LaTeX still contain the document we started with?

This is a genuine round trip, not a spot check. The IR is rendered to LaTeX, the LaTeX
is read *back* through Pandoc into an AST, and that AST is compared to the IR:

    IR  →  Pandoc AST  →  LaTeX  →  Pandoc AST'  →  compare against IR

Anchors are the interesting part. Each one is exported as an identified Span, which
Pandoc's LaTeX writer emits as `\\label{anchor_id}` and its LaTeX reader recovers as a
Span with that identifier. So "every in-text anchor survived" is a set comparison over
IDs read out of the rendered file — not an inference, and not a count that two
compensating errors could satisfy.

The report is deliberately verbose about *what* failed. A round trip that says only
"False" tells you nothing at 2am.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app.core.contracts import Document
from app.export.latex import ExportResult, export_latex
from app.export.pandoc import run_pandoc
from app.ir import ids
from app.ir.traversal import anchor_ids, iter_blocks, paragraph_count, section_titles

__all__ = ["RoundTripReport", "verify_round_trip", "latex_to_ast"]


@dataclass
class RoundTripReport:
    """Every CP-1 round-trip criterion, each independently inspectable."""

    style_id: str

    expected_title: str | None = None
    found_title: str | None = None

    expected_sections: list[str] = field(default_factory=list)
    found_sections: list[str] = field(default_factory=list)

    expected_paragraphs: int = 0
    found_paragraphs: int = 0

    expected_anchors: list[str] = field(default_factory=list)
    found_anchors: list[str] = field(default_factory=list)

    latex: str = ""

    @property
    def title_ok(self) -> bool:
        return _norm(self.expected_title) == _norm(self.found_title)

    @property
    def sections_ok(self) -> bool:
        """Titles *and* order — a reordered paper is a different paper."""
        return [_norm(s) for s in self.expected_sections] == [_norm(s) for s in self.found_sections]

    @property
    def paragraphs_ok(self) -> bool:
        """CP-1 says ±0."""
        return self.expected_paragraphs == self.found_paragraphs

    @property
    def missing_anchors(self) -> list[str]:
        return sorted(set(self.expected_anchors) - set(self.found_anchors))

    @property
    def unexpected_anchors(self) -> list[str]:
        return sorted(set(self.found_anchors) - set(self.expected_anchors))

    @property
    def anchors_ok(self) -> bool:
        return not self.missing_anchors and not self.unexpected_anchors

    @property
    def ok(self) -> bool:
        return self.title_ok and self.sections_ok and self.paragraphs_ok and self.anchors_ok

    def failures(self) -> list[str]:
        """Human-readable reasons, empty iff `ok`."""
        problems: list[str] = []
        if not self.title_ok:
            problems.append(f"title: expected {self.expected_title!r}, found {self.found_title!r}")
        if not self.sections_ok:
            problems.append(
                f"sections: expected {self.expected_sections!r}, found {self.found_sections!r}"
            )
        if not self.paragraphs_ok:
            problems.append(
                f"paragraphs: expected {self.expected_paragraphs}, found {self.found_paragraphs}"
            )
        if self.missing_anchors:
            problems.append(f"anchors lost in export: {self.missing_anchors!r}")
        if self.unexpected_anchors:
            problems.append(f"anchors appeared from nowhere: {self.unexpected_anchors!r}")
        return problems


def _norm(text: str | None) -> str:
    return " ".join((text or "").split())


def latex_to_ast(latex: str) -> dict:
    """Read LaTeX back into a Pandoc AST."""
    return json.loads(run_pandoc(["-f", "latex", "-t", "json"], stdin=latex).stdout)


def _inline_text(inlines: list) -> str:
    """Flatten Pandoc inlines to plain text. Enough for titles and headings."""
    out: list[str] = []
    for node in inlines:
        kind = node.get("t")
        if kind == "Str":
            out.append(node.get("c", ""))
        elif kind in {"Space", "SoftBreak", "LineBreak"}:
            out.append(" ")
        elif kind in {"Emph", "Strong", "SmallCaps", "Strikeout", "Superscript", "Subscript"}:
            out.append(_inline_text(node.get("c") or []))
        elif kind == "Span":
            # Span is [attr, inlines]; the text is the second element.
            content = node.get("c") or []
            out.append(_inline_text(content[1] if len(content) > 1 else []))
        elif kind == "Quoted":
            out.append(_inline_text(node["c"][1]))
        elif kind in {"Code", "RawInline"}:
            out.append(node["c"][1] if isinstance(node.get("c"), list) else "")
    return "".join(out)


def _walk_inlines(inlines: list, span_ids: list[str]) -> None:
    for node in inlines:
        kind = node.get("t")
        content = node.get("c")
        if kind == "Span" and isinstance(content, list) and content:
            attr = content[0]
            if isinstance(attr, list) and attr and attr[0]:
                span_ids.append(attr[0])
            if len(content) > 1 and isinstance(content[1], list):
                _walk_inlines(content[1], span_ids)
        elif kind == "Cite" and isinstance(content, list) and len(content) > 1:
            _walk_inlines(content[1], span_ids)
        elif isinstance(content, list):
            # Emph/Strong/Quoted and friends nest inlines in their last element.
            for item in content:
                if isinstance(item, list) and item and isinstance(item[0], dict):
                    _walk_inlines(item, span_ids)


def _collect(blocks: list, *, headers: list[tuple[int, str]], paragraphs: list[int], span_ids: list[str]) -> None:
    for block in blocks:
        kind = block.get("t")
        content = block.get("c")
        if kind == "Header":
            headers.append((content[0], _inline_text(content[2])))
            _walk_inlines(content[2], span_ids)
        elif kind in {"Para", "Plain"}:
            paragraphs.append(1)
            _walk_inlines(content, span_ids)
        elif kind == "Div":
            # Placeholders (ADR-008) are Divs. Their inner paragraph is counted like any
            # other, which is correct: a figure placeholder occupies a block position.
            _collect(content[1], headers=headers, paragraphs=paragraphs, span_ids=span_ids)
        elif kind == "BlockQuote":
            _collect(content, headers=headers, paragraphs=paragraphs, span_ids=span_ids)
        elif kind in {"BulletList", "OrderedList"}:
            items = content[1] if kind == "OrderedList" else content
            for item in items:
                _collect(item, headers=headers, paragraphs=paragraphs, span_ids=span_ids)


def verify_round_trip(
    doc: Document,
    sources: Mapping[str, dict],
    *,
    style_id: str | None = None,
    styles_dir: Path | None = None,
) -> RoundTripReport:
    """Export, re-read, and compare. Never raises on a *mismatch* — that is the report.

    It does still raise `ExportFailure` if pandoc refuses to render at all, because that
    is not a fidelity question, it is a broken pipeline.
    """
    export: ExportResult = export_latex(
        doc,
        sources,
        style_id=style_id,
        styles_dir=styles_dir,
        # The bibliography's own paragraphs are not the paper's paragraphs; leaving it
        # out keeps the structural comparison honest rather than approximately right.
        suppress_bibliography=True,
        standalone=True,
    )

    ast = latex_to_ast(export.latex)
    headers: list[tuple[int, str]] = []
    paragraphs: list[int] = []
    span_ids: list[str] = []
    _collect(ast.get("blocks", []), headers=headers, paragraphs=paragraphs, span_ids=span_ids)

    meta_title = ast.get("meta", {}).get("title")
    found_title = _inline_text(meta_title["c"]) if meta_title else None

    # Read anchors out of the output on their own terms — by ID prefix, not by asking
    # "is it one of the ones I expected". Matching against the expected set would make
    # a spurious anchor invisible and a lost one indistinguishable from a filter miss.
    expected_anchor_ids = anchor_ids(doc)
    found_anchor_ids = [sid for sid in span_ids if sid.startswith(f"{ids.ANCHOR}_")]

    # Section headers are the only Headers we emit; a paragraph that renders as a
    # heading would show up here as a count mismatch rather than being absorbed.
    return RoundTripReport(
        style_id=export.style_id,
        expected_title=doc.metadata.title,
        found_title=found_title,
        expected_sections=section_titles(doc),
        found_sections=[title for _, title in headers],
        expected_paragraphs=paragraph_count(doc),
        # Placeholder blocks contribute a paragraph each in the output; subtract them so
        # the comparison is paragraph-to-paragraph.
        found_paragraphs=sum(paragraphs) - _placeholder_block_count(doc),
        expected_anchors=expected_anchor_ids,
        found_anchors=found_anchor_ids,
        latex=export.latex,
    )


def _placeholder_block_count(doc: Document) -> int:
    return sum(1 for _, b in iter_blocks(doc) if b.type in {"figure", "table", "equation"})
