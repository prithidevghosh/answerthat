"""Read-only traversal and structural facts about a `Document`.

This module is where the guarantees become checkable. "The citation multiset is
preserved" (HR-5) is not a statement you can make about a LaTeX string; it is
`source_id_multiset(before) <= source_id_multiset(after)`, one line, no model involved.
That is the entire argument for ADR-004, so keep these functions pure and total.

Nothing here mutates. Nothing here raises on a malformed document either — use
`validate` for that, so callers can choose between "tell me what's wrong" and "walk what
is there".
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from app.core.contracts import Block, CitationAnchor, Document, Section, Span

__all__ = [
    "SpanRef", "AnchorRef",
    "iter_blocks", "iter_spans", "iter_anchors",
    "find_section", "find_block", "find_span", "find_anchor",
    "source_id_multiset", "anchor_ids", "span_ids", "section_titles",
    "paragraph_count", "block_count", "text_of_block", "text_of_document",
    "validate", "IRProblem",
]


@dataclass(frozen=True)
class SpanRef:
    """A span together with the block and section that contain it."""

    section: Section
    block: Block
    span: Span


@dataclass(frozen=True)
class AnchorRef:
    """An anchor together with its full ancestry — what you need to relocate it."""

    section: Section
    block: Block
    span: Span
    anchor: CitationAnchor


def iter_blocks(doc: Document) -> Iterator[tuple[Section, Block]]:
    for section in doc.sections:
        for block in section.blocks:
            yield section, block


def iter_spans(doc: Document) -> Iterator[SpanRef]:
    for section, block in iter_blocks(doc):
        for span in block.spans:
            yield SpanRef(section, block, span)


def iter_anchors(doc: Document) -> Iterator[AnchorRef]:
    for ref in iter_spans(doc):
        for anchor in ref.span.citation_anchors:
            yield AnchorRef(ref.section, ref.block, ref.span, anchor)


def find_section(doc: Document, section_id: str) -> Section | None:
    return next((s for s in doc.sections if s.id == section_id), None)


def find_block(doc: Document, block_id: str) -> tuple[Section, Block] | None:
    return next((pair for pair in iter_blocks(doc) if pair[1].id == block_id), None)


def find_span(doc: Document, span_id: str) -> SpanRef | None:
    return next((r for r in iter_spans(doc) if r.span.id == span_id), None)


def find_anchor(doc: Document, anchor_id: str) -> AnchorRef | None:
    return next((r for r in iter_anchors(doc) if r.anchor.anchor_id == anchor_id), None)


def source_id_multiset(doc: Document) -> Counter[str]:
    """Every `source_id` reachable from the document, with multiplicity.

    A multiset, not a set: a paper cited three times that ends up cited once has lost
    two citations, and a set comparison would call that unchanged. HR-5 is about the
    multiset.
    """
    counts: Counter[str] = Counter()
    for ref in iter_anchors(doc):
        counts.update(ref.anchor.source_ids)
    return counts


def anchor_ids(doc: Document) -> list[str]:
    return [r.anchor.anchor_id for r in iter_anchors(doc)]


def span_ids(doc: Document) -> list[str]:
    return [r.span.id for r in iter_spans(doc)]


def section_titles(doc: Document) -> list[str]:
    """Section titles in document order — the round-trip check compares these."""
    return [s.title for s in sorted(doc.sections, key=lambda s: s.order)]


def paragraph_count(doc: Document) -> int:
    """Paragraph blocks only. CP-1 requires this to survive the round trip at ±0."""
    return sum(1 for _, block in iter_blocks(doc) if block.type == "paragraph")


def block_count(doc: Document) -> int:
    return sum(1 for _ in iter_blocks(doc))


def text_of_block(block: Block) -> str:
    """Concatenated span text. Text lives only in spans (ADR-004)."""
    return "".join(span.text for span in block.spans)


def text_of_document(doc: Document) -> str:
    return "\n\n".join(text_of_block(b) for _, b in iter_blocks(doc))


@dataclass(frozen=True)
class IRProblem:
    """A structural defect. `where` is the ID of the offending node when there is one."""

    code: str
    message: str
    where: str | None = None

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"[{self.code}] {self.message}" + (f" (at {self.where})" if self.where else "")


def validate(doc: Document, known_source_ids: set[str] | None = None) -> list[IRProblem]:
    """Structural checks. Returns problems; never raises, never repairs.

    Pass `known_source_ids` (the keys of `source_store`) to also check HR-1: every
    `source_id` an anchor points at must exist. Omit it and that check is skipped —
    the IR layer has no business reaching into the store itself.
    """
    problems: list[IRProblem] = []
    seen: dict[str, str] = {}

    def _unique(kind: str, node_id: str) -> None:
        if node_id in seen:
            problems.append(
                IRProblem("duplicate_id", f"{kind} id reused (first seen as {seen[node_id]})", node_id)
            )
        else:
            seen[node_id] = kind

    for section in doc.sections:
        _unique("section", section.id)
        if section.level < 1:
            problems.append(IRProblem("bad_level", f"section level {section.level} < 1", section.id))
        for block in section.blocks:
            _unique("block", block.id)
            if block.type in {"figure", "table", "equation"} and not block.placeholder_caption:
                # ADR-008: the placeholder must carry its caption and be visible. A
                # caption-less placeholder is an invisible scope cut, which is the
                # failure mode ADR-008 exists to avoid.
                problems.append(
                    IRProblem("placeholder_without_caption", f"{block.type} block has no caption", block.id)
                )
            for span in block.spans:
                _unique("span", span.id)
                for anchor in span.citation_anchors:
                    _unique("anchor", anchor.anchor_id)
                    if not 0 <= anchor.offset_in_span <= len(span.text):
                        problems.append(
                            IRProblem(
                                "anchor_out_of_range",
                                f"offset {anchor.offset_in_span} outside span of length {len(span.text)}",
                                anchor.anchor_id,
                            )
                        )
                    if known_source_ids is not None:
                        for sid in anchor.source_ids:
                            if sid not in known_source_ids:
                                problems.append(
                                    IRProblem(
                                        "unknown_source_id",
                                        f"source_id {sid!r} is not in source_store (HR-1)",
                                        anchor.anchor_id,
                                    )
                                )

    orders = [s.order for s in doc.sections]
    if len(set(orders)) != len(orders):
        problems.append(IRProblem("duplicate_section_order", "two sections share an order value"))

    return problems
