"""The partial-IR fragment format, and the pure function that applies one.

`ProposedChange.new_fragment` is typed as `dict` in Appendix A. This module fixes what
that dict means, so the executor, the kernel and the diff builder all agree — and so the
kernel can compute the *after* document deterministically, with no model in the loop.

A fragment never contains free text. It contains whole IR nodes that replace, insert,
delete or move existing ones by id.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy

from pydantic import BaseModel, Field

from app.core.contracts import Block, CitationAnchor, Document, Section, Span


class BlockInsertion(BaseModel):
    section_id: str
    block: Block
    after_block_id: str | None = None  # None → prepend to the section


class BlockMove(BaseModel):
    block_id: str
    to_section_id: str
    after_block_id: str | None = None


class Fragment(BaseModel):
    """A partial IR change set. Every list is applied in the order declared below."""

    replace_spans: list[Span] = Field(default_factory=list)
    replace_blocks: list[Block] = Field(default_factory=list)
    replace_sections: list[Section] = Field(default_factory=list)
    insert_blocks: list[BlockInsertion] = Field(default_factory=list)
    delete_block_ids: list[str] = Field(default_factory=list)
    move_blocks: list[BlockMove] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.replace_spans
            or self.replace_blocks
            or self.replace_sections
            or self.insert_blocks
            or self.delete_block_ids
            or self.move_blocks
        )


class FragmentApplicationError(RuntimeError):
    """A fragment referenced a node that does not exist. Never swallowed (HR-3)."""


def parse_fragment(raw: dict) -> Fragment:
    return Fragment.model_validate(raw)


# --------------------------------------------------------------------------- helpers


def iter_spans(document: Document):
    for section in document.sections:
        for block in section.blocks:
            for span in block.spans:
                yield section, block, span


def iter_blocks(document: Document):
    for section in document.sections:
        for block in section.blocks:
            yield section, block


def iter_anchors(document: Document):
    for _section, _block, span in iter_spans(document):
        for anchor in span.citation_anchors:
            yield span, anchor


def source_multiset(document: Document) -> Counter[str]:
    """The multiset of `source_id`s reachable from the document. HR-5 is defined on this."""
    counts: Counter[str] = Counter()
    for _span, anchor in iter_anchors(document):
        counts.update(anchor.source_ids)
    return counts


def anchors_by_id(document: Document) -> dict[str, tuple[str, CitationAnchor]]:
    """anchor_id → (span_id, anchor)."""
    out: dict[str, tuple[str, CitationAnchor]] = {}
    for span, anchor in iter_anchors(document):
        out[anchor.anchor_id] = (span.id, anchor)
    return out


def _renumber(section: Section) -> None:
    for index, block in enumerate(section.blocks):
        block.order = index


# --------------------------------------------------------------------------- apply


def apply_fragment(document: Document, fragment: Fragment) -> Document:
    """Return a new Document with the fragment applied. Pure: `document` is untouched.

    Raises FragmentApplicationError when the fragment points at a node that is not there.
    That is a defect in the executor, not a user-visible outcome, and it must be loud.
    """
    doc = deepcopy(document)

    section_index = {s.id: s for s in doc.sections}
    block_index = {b.id: (s, b) for s, b in iter_blocks(doc)}
    span_index = {sp.id: (s, b, sp) for s, b, sp in iter_spans(doc)}

    for new_span in fragment.replace_spans:
        target = span_index.get(new_span.id)
        if target is None:
            raise FragmentApplicationError(f"replace_spans: unknown span id {new_span.id!r}")
        _sec, block, old = target
        block.spans[block.spans.index(old)] = deepcopy(new_span)

    for new_block in fragment.replace_blocks:
        target_block = block_index.get(new_block.id)
        if target_block is None:
            raise FragmentApplicationError(f"replace_blocks: unknown block id {new_block.id!r}")
        section, old_block = target_block
        position = section.blocks.index(old_block)
        replacement_block = deepcopy(new_block)
        replacement_block.order = old_block.order
        section.blocks[position] = replacement_block

    for new_section in fragment.replace_sections:
        old_section = section_index.get(new_section.id)
        if old_section is None:
            raise FragmentApplicationError(f"replace_sections: unknown section id {new_section.id!r}")
        position = doc.sections.index(old_section)
        replacement_section = deepcopy(new_section)
        replacement_section.order = old_section.order
        doc.sections[position] = replacement_section

    # Indices are stale after whole-node replacement; rebuild before structural edits.
    section_index = {s.id: s for s in doc.sections}

    for insertion in fragment.insert_blocks:
        section = section_index.get(insertion.section_id)
        if section is None:
            raise FragmentApplicationError(f"insert_blocks: unknown section id {insertion.section_id!r}")
        block = deepcopy(insertion.block)
        if insertion.after_block_id is None:
            section.blocks.insert(0, block)
        else:
            ids = [b.id for b in section.blocks]
            if insertion.after_block_id not in ids:
                raise FragmentApplicationError(
                    f"insert_blocks: after_block_id {insertion.after_block_id!r} not in section {section.id!r}"
                )
            section.blocks.insert(ids.index(insertion.after_block_id) + 1, block)
        _renumber(section)

    if fragment.delete_block_ids:
        doomed = set(fragment.delete_block_ids)
        present = {b.id for _s, b in iter_blocks(doc)}
        missing = doomed - present
        if missing:
            raise FragmentApplicationError(f"delete_block_ids: unknown block ids {sorted(missing)}")
        for section in doc.sections:
            section.blocks = [b for b in section.blocks if b.id not in doomed]
            _renumber(section)

    for move in fragment.move_blocks:
        origin = next((s for s in doc.sections if any(b.id == move.block_id for b in s.blocks)), None)
        if origin is None:
            raise FragmentApplicationError(f"move_blocks: unknown block id {move.block_id!r}")
        block = next(b for b in origin.blocks if b.id == move.block_id)
        destination = section_index.get(move.to_section_id)
        if destination is None:
            raise FragmentApplicationError(f"move_blocks: unknown section id {move.to_section_id!r}")
        origin.blocks.remove(block)
        if move.after_block_id is None:
            destination.blocks.insert(0, block)
        else:
            ids = [b.id for b in destination.blocks]
            if move.after_block_id not in ids:
                raise FragmentApplicationError(
                    f"move_blocks: after_block_id {move.after_block_id!r} not in section {destination.id!r}"
                )
            destination.blocks.insert(ids.index(move.after_block_id) + 1, block)
        _renumber(origin)
        _renumber(destination)

    return doc


__all__ = [
    "BlockInsertion",
    "BlockMove",
    "Fragment",
    "FragmentApplicationError",
    "anchors_by_id",
    "apply_fragment",
    "iter_anchors",
    "iter_blocks",
    "iter_spans",
    "parse_fragment",
    "source_multiset",
]
