"""IR → LaTeX, via Pandoc and citeproc.

This is the only place a `.tex` file is produced. There is deliberately no default
style: rendering a paper in the wrong citation style without saying so is a quiet
substitution, and quiet substitutions are what HR-3 exists to prevent. The caller passes
a `style_id`, or the document carries one from style detection, or the export refuses.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app.core.contracts import Document
from app.core.errors import ExportFailure
from app.export.ast import citation_key_map, document_to_ast
from app.export.pandoc import PandocResult, ast_to_latex
from app.export.styles import SHORTLIST, style_path
from app.export.unicode_latex import pdftex_unicode_preamble, scan_json_strings
from app.ir.traversal import source_id_multiset

__all__ = ["ExportResult", "export_latex", "build_bibliography"]


@dataclass(frozen=True)
class ExportResult:
    latex: str
    style_id: str
    csl_path: Path
    # source_id -> citation key actually used in the document, for the audit view.
    citation_keys: dict[str, str] = field(default_factory=dict)
    cited_source_ids: list[str] = field(default_factory=list)


def _resolve_style(doc: Document, style_id: str | None) -> str:
    chosen = style_id or doc.metadata.style_id
    if not chosen:
        raise ExportFailure(
            "no citation style selected. Pass style_id=, or run style detection and store "
            "the result on document.metadata.style_id. There is no default: rendering a "
            "paper in the wrong style without saying so is exactly the silent substitution "
            f"HR-3 forbids. Available: {', '.join(sorted(SHORTLIST))}."
        )
    return chosen


def build_bibliography(
    doc: Document,
    sources: Mapping[str, dict],
    keys: Mapping[str, str],
) -> list[dict]:
    """CSL-JSON for every source the document actually cites, keyed for pandoc.

    A cited source with no CSL record is an error, not an omission. Rendering around it
    would drop a citation from the bibliography while leaving its marker in the text.
    """
    cited = source_id_multiset(doc)
    missing = [sid for sid in cited if sid not in sources]
    if missing:
        raise ExportFailure(
            "cannot build a bibliography: no CSL-JSON supplied for cited source_id(s) "
            f"{missing!r}. Every citation must resolve to a record; dropping one would "
            "leave a marker in the text pointing at nothing (HR-1/HR-5)."
        )

    entries: list[dict] = []
    for source_id in sorted(cited):
        entry = dict(sources[source_id])
        entry["id"] = keys[source_id]
        entries.append(entry)
    return entries


def _apply_unicode_preamble(ast: dict, preamble: str | None) -> None:
    """Put the character declarations on the AST's `header-includes`.

    See `unicode_latex` for why they are needed: without them the `.tex` is valid but
    only builds under xelatex/lualatex, and every pdflatex-based service — which is most
    of them — stops on the first Greek letter.
    """
    if preamble is None:
        ast["meta"].pop("header-includes", None)
        return
    ast["meta"]["header-includes"] = {
        "t": "MetaBlocks",
        "c": [{"t": "RawBlock", "c": ["latex", preamble]}],
    }


def export_latex(
    doc: Document,
    sources: Mapping[str, dict],
    *,
    style_id: str | None = None,
    styles_dir: Path | None = None,
    suppress_bibliography: bool = False,
    standalone: bool = True,
) -> ExportResult:
    """Render the document to LaTeX.

    `sources` maps `source_id` to CSL-JSON. We only read it — writes to `source_store`
    belong to provider adapters alone (HR-1).

    `suppress_bibliography` renders citations but omits the reference list. It exists
    for the structural round-trip check, which compares document shape and would
    otherwise have to distinguish the bibliography's paragraphs from the paper's.
    """
    chosen_style = _resolve_style(doc, style_id)
    csl = style_path(chosen_style, styles_dir)

    cited = sorted(source_id_multiset(doc))
    keys = citation_key_map(cited)
    bibliography = build_bibliography(doc, sources, keys)
    ast = document_to_ast(doc, keys)

    if suppress_bibliography:
        ast["meta"]["suppress-bibliography"] = {"t": "MetaBool", "c": True}

    # Everything that can reach the output: the document itself, and the CSL-JSON that
    # citeproc will format into the bibliography.
    scanned = scan_json_strings(
        json.dumps(ast, ensure_ascii=False) + json.dumps(bibliography, ensure_ascii=False)
    )
    preamble = pdftex_unicode_preamble(scanned)
    _apply_unicode_preamble(ast, preamble)

    def render() -> PandocResult:
        return ast_to_latex(ast, csl_path=csl, bibliography=bibliography, standalone=standalone)

    result: PandocResult = render()

    # citeproc emits characters of its own, from the CSL style and its locale, which no
    # pre-scan of our inputs can see. If the rendered file turns out to need declarations
    # we did not make, make them and render once more. One retry is enough: the only
    # thing the second pass adds to the file is the ASCII of the declarations themselves.
    if standalone:
        widened = pdftex_unicode_preamble(scanned + result.stdout)
        if widened != preamble:
            _apply_unicode_preamble(ast, widened)
            result = render()

    return ExportResult(
        latex=result.stdout,
        style_id=chosen_style,
        csl_path=csl,
        citation_keys=dict(keys),
        cited_source_ids=cited,
    )
