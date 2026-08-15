"""Construction helpers for the IR.

The TEI mapper and the tests both need to build documents, and both need the same ID
discipline. Doing it by hand in two places is how the two drift apart, so it lives here.

The builder assigns stable, position-derived IDs (see `ids.stable_id`) and keeps `order`
fields consistent, which is the part that is easy to get subtly wrong and expensive to
debug later — an out-of-order section renders in the wrong place in the export and looks
like a Pandoc bug.
"""

from __future__ import annotations

from app.core.contracts import (
    Block,
    CitationAnchor,
    Document,
    DocumentMeta,
    QuarantineEntry,
    Section,
    Span,
)
from app.ir import ids

__all__ = ["DocumentBuilder", "SectionBuilder", "BlockBuilder"]

BlockType = str  # Literal["paragraph","equation","figure","table","list"] — see contracts


class BlockBuilder:
    def __init__(self, doc_id: str, section_order: int, order: int, block_type: BlockType,
                 placeholder_caption: str | None = None) -> None:
        self._doc_id = doc_id
        self._section_order = section_order
        self._order = order
        self._type = block_type
        self._caption = placeholder_caption
        self._spans: list[Span] = []

    def span(self, text: str, *, span_index: int | None = None) -> Span:
        """Append a span. Text lives only here (ADR-004)."""
        index = len(self._spans) if span_index is None else span_index
        span = Span(
            id=ids.stable_id(ids.SPAN, self._doc_id, self._section_order, self._order, index),
            text=text,
        )
        self._spans.append(span)
        return span

    def anchor(
        self,
        span: Span,
        *,
        source_ids: list[str],
        offset_in_span: int,
        original_marker_text: str | None = None,
        marker_index: int | None = None,
        confidence: float = 1.0,
        provenance_kind: str = "parsed",
    ) -> CitationAnchor:
        """Attach a citation anchor to a span.

        An anchor is a node with an ID, not characters inside the text. The marker text
        is kept for the audit view and for style detection, but the anchor's position is
        `offset_in_span`, and removing the anchor does not require touching the string.
        """
        index = len(span.citation_anchors) if marker_index is None else marker_index
        anchor = CitationAnchor(
            anchor_id=ids.stable_id(ids.ANCHOR, self._doc_id, span.id, index),
            source_ids=list(source_ids),
            offset_in_span=offset_in_span,
            original_marker_text=original_marker_text,
            confidence=confidence,
            provenance_kind=provenance_kind,  # type: ignore[arg-type]
        )
        span.citation_anchors.append(anchor)
        return anchor

    def build(self) -> Block:
        return Block(
            id=ids.stable_id(ids.BLOCK, self._doc_id, self._section_order, self._order),
            type=self._type,  # type: ignore[arg-type]
            order=self._order,
            spans=self._spans,
            placeholder_caption=self._caption,
        )


class SectionBuilder:
    def __init__(self, doc_id: str, order: int, title: str, level: int) -> None:
        self._doc_id = doc_id
        self._order = order
        self._title = title
        self._level = level
        self._blocks: list[BlockBuilder] = []

    def block(self, block_type: BlockType = "paragraph", *, caption: str | None = None) -> BlockBuilder:
        builder = BlockBuilder(self._doc_id, self._order, len(self._blocks), block_type, caption)
        self._blocks.append(builder)
        return builder

    def paragraph(self, text: str) -> tuple[BlockBuilder, Span]:
        """The common case: one paragraph block holding one span."""
        block = self.block("paragraph")
        return block, block.span(text)

    def placeholder(self, block_type: BlockType, caption: str) -> BlockBuilder:
        """A figure/table/equation placeholder. ADR-008 requires the caption."""
        if block_type not in {"figure", "table", "equation"}:
            raise ValueError(f"{block_type} is not a placeholder block type")
        return self.block(block_type, caption=caption)

    def build(self) -> Section:
        return Section(
            id=ids.stable_id(ids.SECTION, self._doc_id, self._order),
            level=self._level,
            title=self._title,
            order=self._order,
            blocks=[b.build() for b in self._blocks],
        )


class DocumentBuilder:
    def __init__(self, doc_id: str, *, title: str | None = None) -> None:
        self._doc_id = doc_id
        self._meta = DocumentMeta(title=title)
        self._sections: list[SectionBuilder] = []
        self._quarantine: list[QuarantineEntry] = []

    @property
    def doc_id(self) -> str:
        return self._doc_id

    def section(self, title: str, *, level: int = 1) -> SectionBuilder:
        builder = SectionBuilder(self._doc_id, len(self._sections), title, level)
        self._sections.append(builder)
        return builder

    def quarantine(self, raw: str, reason: str, page: int | None = None) -> None:
        """Park an entry we could not parse. The raw string is kept verbatim (HR-3)."""
        self._quarantine.append(QuarantineEntry(raw=raw, reason=reason, page=page))  # type: ignore[arg-type]

    def build(self, *, version: int = 1) -> Document:
        return Document(
            doc_id=self._doc_id,
            version=version,
            metadata=self._meta,
            sections=[s.build() for s in self._sections],
            quarantine=list(self._quarantine),
        )
