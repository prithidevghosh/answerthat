"""IR → Pandoc AST.

Two decisions here carry weight.

**Anchors become identified Spans wrapping a Cite.** Pandoc's LaTeX writer renders
`Span (id, ...) [...]` as `\\protect\\phantomsection\\label{id}{...}`, which means every
`anchor_id` survives literally into the exported `.tex` while citeproc still does all the
formatting. That gives us HR-4 and a mechanically checkable round trip at the same time:
we do not have to *infer* that an anchor survived, we can grep for it.

**Text is emitted as Str/Space tokens, never as a formatted string.** Pandoc's writer
escapes LaTeX special characters when it serialises `Str`, so a paper containing `$`,
`%`, `\\` or `&` renders correctly and cannot inject markup. Building a LaTeX string
ourselves and hoping we escaped everything is how that goes wrong.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from app.core.contracts import Block, CitationAnchor, Document, Section, Span
from app.core.errors import ExportFailure
from app.export.pandoc import PANDOC_API_VERSION

__all__ = ["document_to_ast", "citation_key_map", "PLACEHOLDER_PREFIXES"]

# ADR-008: figures, tables and equations are a stated scope cut. The placeholder must be
# visible in the output, so nobody mistakes it for fidelity we do not have.
PLACEHOLDER_PREFIXES: dict[str, str] = {
    "figure": "[FIGURE NOT REPRODUCED]",
    "table": "[TABLE NOT REPRODUCED]",
    "equation": "[EQUATION NOT REPRODUCED]",
}

_KEY_SAFE = re.compile(r"[^A-Za-z0-9_:.#$%&+?<>~/-]")


def citation_key_map(source_ids: Sequence[str]) -> dict[str, str]:
    """Map each `source_id` to a citation key that is stable and pandoc-safe.

    Source ids can be DOIs or provider ids containing characters that are awkward as
    citation keys. The mapping is deterministic and collision-checked: two different
    sources may never share a key, because that would silently merge two citations into
    one and shrink the bibliography.
    """
    mapping: dict[str, str] = {}
    used: dict[str, str] = {}
    for source_id in source_ids:
        if source_id in mapping:
            continue
        base = _KEY_SAFE.sub("-", source_id).strip("-") or "src"
        key = base
        suffix = 2
        while key in used and used[key] != source_id:
            key = f"{base}-{suffix}"
            suffix += 1
        mapping[source_id] = key
        used[key] = source_id
    return mapping


def _inlines_from_text(text: str) -> list[dict]:
    """Plain text → Pandoc inlines. Whitespace becomes Space/SoftBreak, never Str."""
    inlines: list[dict] = []
    for index, line in enumerate(text.split("\n")):
        if index:
            inlines.append({"t": "SoftBreak"})
        first = True
        for token in line.split(" "):
            if not first:
                inlines.append({"t": "Space"})
            first = False
            if token:
                inlines.append({"t": "Str", "c": token})
    return inlines


def _cite_inline(anchor: CitationAnchor, keys: Mapping[str, str]) -> dict:
    """A Cite wrapped in a Span carrying the anchor's own id.

    Multiple `source_ids` on one anchor become multiple citations inside a single Cite,
    which is what collapses `[2, 3]` back into one bracket group under a numeric style.
    """
    citations = []
    for source_id in anchor.source_ids:
        key = keys.get(source_id)
        if key is None:
            raise ExportFailure(
                f"anchor {anchor.anchor_id!r} cites source_id {source_id!r}, which is not in the "
                "supplied bibliography. Rendering it would silently drop a citation, so the "
                "export stops here (HR-3/HR-5)."
            )
        citations.append(
            {
                "citationId": key,
                "citationPrefix": _inlines_from_text(anchor.prefix) if anchor.prefix else [],
                "citationSuffix": _inlines_from_text(anchor.locator) if anchor.locator else [],
                "citationMode": {"t": "NormalCitation"},
                "citationNoteNum": 0,
                "citationHash": 0,
            }
        )
    fallback = [{"t": "Str", "c": "[" + "; ".join(f"@{c['citationId']}" for c in citations) + "]"}]
    cite = {"t": "Cite", "c": [citations, fallback]}
    return {"t": "Span", "c": [[anchor.anchor_id, [], []], [cite]]}


def _span_to_inlines(span: Span, keys: Mapping[str, str]) -> list[dict]:
    """Split the span's text at each anchor offset and interleave the citations."""
    anchors = sorted(span.citation_anchors, key=lambda a: a.offset_in_span)
    inlines: list[dict] = []
    cursor = 0
    for anchor in anchors:
        offset = max(0, min(anchor.offset_in_span, len(span.text)))
        if offset > cursor:
            inlines.extend(_inlines_from_text(span.text[cursor:offset]))
        inlines.append(_cite_inline(anchor, keys))
        cursor = offset
    if cursor < len(span.text):
        inlines.extend(_inlines_from_text(span.text[cursor:]))
    return inlines


def _block_to_ast(block: Block, keys: Mapping[str, str]) -> list[dict]:
    if block.type in PLACEHOLDER_PREFIXES:
        return [_placeholder_block(block)]

    inlines: list[dict] = []
    for index, span in enumerate(block.spans):
        if index:
            inlines.append({"t": "Space"})
        inlines.extend(_span_to_inlines(span, keys))

    if block.type == "list":
        items = [[{"t": "Plain", "c": _span_to_inlines(span, keys)}] for span in block.spans]
        return [{"t": "BulletList", "c": items}]

    if not inlines:
        # An empty paragraph still occupies a position in the document; dropping it
        # would move the paragraph count off by one and break the CP-1 round trip.
        inlines = [{"t": "Str", "c": ""}]
    return [{"t": "Para", "c": inlines}]


def _placeholder_block(block: Block) -> dict:
    """A visible placeholder carrying its caption. ADR-008."""
    label = PLACEHOLDER_PREFIXES[block.type]
    caption = block.placeholder_caption or "(no caption extracted)"
    inlines: list[dict] = [
        {"t": "Strong", "c": [{"t": "Str", "c": label}]},
        {"t": "Space"},
        *_inlines_from_text(caption),
    ]
    return {
        "t": "Div",
        "c": [[block.id, ["answerthat-placeholder", block.type], []], [{"t": "Para", "c": inlines}]],
    }


def _section_to_ast(section: Section, keys: Mapping[str, str]) -> list[dict]:
    blocks: list[dict] = [
        {
            "t": "Header",
            "c": [max(1, section.level), [section.id, [], []], _inlines_from_text(section.title)],
        }
    ]
    for block in sorted(section.blocks, key=lambda b: b.order):
        blocks.extend(_block_to_ast(block, keys))
    return blocks


def document_to_ast(doc: Document, keys: Mapping[str, str]) -> dict:
    """Full document → Pandoc AST. `keys` comes from `citation_key_map`."""
    blocks: list[dict] = []
    for section in sorted(doc.sections, key=lambda s: s.order):
        blocks.extend(_section_to_ast(section, keys))

    meta: dict = {}
    if doc.metadata.title:
        meta["title"] = {
            "t": "MetaInlines",
            "c": _inlines_from_text(doc.metadata.title),
        }
    return {"pandoc-api-version": PANDOC_API_VERSION, "meta": meta, "blocks": blocks}
