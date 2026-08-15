"""Export: IR → Pandoc → LaTeX, and the round-trip check that keeps it honest.

Every citation and every bibliography in this system is rendered here, by citeproc,
reading a real `.csl` file from `packages/csl-styles/` (HR-4). If you are about to build
a citation string with an f-string, a template, or a regex, you are in the wrong module
and the wrong system.
"""

from app.export.ast import citation_key_map, document_to_ast
from app.export.latex import ExportResult, build_bibliography, export_latex
from app.export.pandoc import (
    PandocResult,
    pandoc_available,
    render_bibliography_entries,
    run_pandoc,
)
from app.export.roundtrip import RoundTripReport, verify_round_trip
from app.export.styles import (
    SHORTLIST,
    MarkerFamily,
    StyleInfo,
    all_styles,
    style_path,
    styles_for_family,
)

__all__ = [
    "ExportResult", "export_latex", "build_bibliography",
    "document_to_ast", "citation_key_map",
    "PandocResult", "run_pandoc", "pandoc_available", "render_bibliography_entries",
    "RoundTripReport", "verify_round_trip",
    "SHORTLIST", "StyleInfo", "MarkerFamily", "style_path", "styles_for_family", "all_styles",
]
