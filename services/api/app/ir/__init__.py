"""Document IR: construction, traversal, structural diff, and versioned persistence.

The IR itself (`Document`, `Section`, `Block`, `Span`, `CitationAnchor`) is defined in
`app.core.contracts` and is frozen. This package is everything you do *with* it.

ADR-004 in one line: text lives only in spans, citation anchors are nodes with stable
IDs, and LaTeX is a render target rather than the working representation.
"""

from app.ir.builder import BlockBuilder, DocumentBuilder, SectionBuilder

# Exported as `diff_documents` so the name does not shadow the `app.ir.diff` module
# for anyone doing `from app.ir import diff`.
from app.ir.diff import IRDiff
from app.ir.diff import diff as diff_documents
from app.ir.store import (
    DocumentStore,
    InMemoryDocumentStore,
    PostgresDocumentStore,
    VersionInfo,
)
from app.ir.traversal import (
    AnchorRef,
    IRProblem,
    SpanRef,
    anchor_ids,
    iter_anchors,
    iter_blocks,
    iter_spans,
    paragraph_count,
    section_titles,
    source_id_multiset,
    validate,
)

__all__ = [
    "DocumentBuilder", "SectionBuilder", "BlockBuilder",
    "IRDiff", "diff_documents",
    "DocumentStore", "InMemoryDocumentStore", "PostgresDocumentStore", "VersionInfo",
    "SpanRef", "AnchorRef", "IRProblem",
    "iter_blocks", "iter_spans", "iter_anchors",
    "source_id_multiset", "anchor_ids", "section_titles", "paragraph_count", "validate",
]
