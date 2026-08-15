"""Structural diff — not a textual one.

A text diff of a rewritten paragraph is a wall of red and green in which a citation
either survived or didn't, and you cannot tell which. This diff is computed over the IR,
so it can state the thing the user actually needs to know: **every citation anchor, where
it was, where it is now, and that the multiset did not shrink.**

That visible persistence is how a user comes to trust HR-5 rather than taking our word
for it — which is why `CitationLedger` is the first thing in the payload and not a
footnote at the bottom.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.fragment import anchors_by_id, iter_blocks, iter_spans, source_multiset
from app.core.contracts import Document

AnchorStatus = Literal["unchanged", "moved", "source_changed", "added", "held_for_decision", "removed"]


class AnchorDelta(BaseModel):
    anchor_id: str
    status: AnchorStatus
    marker: str | None = None
    before_span_id: str | None = None
    after_span_id: str | None = None
    source_ids_before: list[str] = Field(default_factory=list)
    source_ids_after: list[str] = Field(default_factory=list)
    note: str | None = None


class CitationLedger(BaseModel):
    """The HR-5 statement, made checkable by the reader."""

    preserved: bool
    total_before: int
    total_after: int
    sources_lost: dict[str, int] = Field(default_factory=dict)
    sources_gained: dict[str, int] = Field(default_factory=dict)
    anchors: list[AnchorDelta] = Field(default_factory=list)
    held_for_decision: list[str] = Field(default_factory=list)

    @property
    def headline(self) -> str:
        if self.preserved and not self.sources_gained:
            return f"All {self.total_before} citations preserved."
        if self.preserved:
            gained = sum(self.sources_gained.values())
            return f"All {self.total_before} citations preserved, {gained} added."
        lost = sum(self.sources_lost.values())
        return f"{lost} citation(s) would be removed — this requires your approval."


class SpanDelta(BaseModel):
    status: Literal["added", "removed", "modified", "unchanged"]
    span_id: str
    before_text: str | None = None
    after_text: str | None = None
    anchor_ids: list[str] = Field(default_factory=list)


class BlockDelta(BaseModel):
    status: Literal["added", "removed", "modified", "moved", "unchanged"]
    block_id: str
    before_section_id: str | None = None
    after_section_id: str | None = None
    spans: list[SpanDelta] = Field(default_factory=list)


class StructuralDiff(BaseModel):
    doc_id: str
    base_version: int
    citations: CitationLedger
    blocks: list[BlockDelta] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.blocks


def build_diff(
    before: Document,
    after: Document,
    *,
    held_anchor_ids: list[str] | None = None,
) -> StructuralDiff:
    held = list(held_anchor_ids or [])
    return StructuralDiff(
        doc_id=before.doc_id,
        base_version=before.version,
        citations=_ledger(before, after, held),
        blocks=_blocks(before, after),
    )


# --------------------------------------------------------------------------- citations


def _ledger(before: Document, after: Document, held: list[str]) -> CitationLedger:
    before_anchors = anchors_by_id(before)
    after_anchors = anchors_by_id(after)
    before_counts = source_multiset(before)
    after_counts = source_multiset(after)

    held_counts: Counter[str] = Counter()
    for anchor_id in held:
        entry = before_anchors.get(anchor_id)
        if entry is not None and anchor_id not in after_anchors:
            held_counts.update(entry[1].source_ids)

    lost = before_counts - (after_counts + held_counts)
    gained = after_counts - before_counts

    deltas: list[AnchorDelta] = []
    for anchor_id in sorted(set(before_anchors) | set(after_anchors)):
        was = before_anchors.get(anchor_id)
        now = after_anchors.get(anchor_id)

        if was and now:
            before_span, before_anchor = was
            after_span, after_anchor = now
            if before_anchor.source_ids != after_anchor.source_ids:
                status: AnchorStatus = "source_changed"
                note = "the citation stayed in place; the source behind it changed"
            elif before_span != after_span:
                status, note = "moved", "the citation followed its sentence"
            else:
                status, note = "unchanged", None
            deltas.append(
                AnchorDelta(
                    anchor_id=anchor_id,
                    status=status,
                    marker=before_anchor.original_marker_text,
                    before_span_id=before_span,
                    after_span_id=after_span,
                    source_ids_before=before_anchor.source_ids,
                    source_ids_after=after_anchor.source_ids,
                    note=note,
                )
            )
        elif now:
            after_span, after_anchor = now
            deltas.append(
                AnchorDelta(
                    anchor_id=anchor_id,
                    status="added",
                    after_span_id=after_span,
                    source_ids_after=after_anchor.source_ids,
                    note="new citation, verified before it was proposed",
                )
            )
        else:
            before_span, before_anchor = was  # type: ignore[misc]
            is_held = anchor_id in held
            deltas.append(
                AnchorDelta(
                    anchor_id=anchor_id,
                    status="held_for_decision" if is_held else "removed",
                    marker=before_anchor.original_marker_text,
                    before_span_id=before_span,
                    source_ids_before=before_anchor.source_ids,
                    note=(
                        "could not be placed confidently — waiting on your decision: "
                        "keep, move, or remove"
                        if is_held
                        else "removed"
                    ),
                )
            )

    return CitationLedger(
        preserved=not lost,
        total_before=sum(before_counts.values()),
        total_after=sum(after_counts.values()),
        sources_lost=dict(lost),
        sources_gained=dict(gained),
        anchors=deltas,
        held_for_decision=held,
    )


# --------------------------------------------------------------------------- structure


def _blocks(before: Document, after: Document) -> list[BlockDelta]:
    before_blocks = {b.id: (s.id, b) for s, b in iter_blocks(before)}
    after_blocks = {b.id: (s.id, b) for s, b in iter_blocks(after)}
    before_spans = {sp.id: sp for _s, _b, sp in iter_spans(before)}

    deltas: list[BlockDelta] = []

    for block_id, (section_id, block) in after_blocks.items():
        previous = before_blocks.get(block_id)
        if previous is None:
            deltas.append(
                BlockDelta(
                    status="added",
                    block_id=block_id,
                    after_section_id=section_id,
                    spans=[
                        SpanDelta(
                            status="added",
                            span_id=span.id,
                            after_text=span.text,
                            anchor_ids=[a.anchor_id for a in span.citation_anchors],
                        )
                        for span in block.spans
                    ],
                )
            )
            continue

        old_section_id, old_block = previous
        span_deltas = _spans(old_block, block, before_spans)
        if not span_deltas and old_section_id == section_id:
            continue
        deltas.append(
            BlockDelta(
                status="moved" if old_section_id != section_id and not span_deltas else "modified",
                block_id=block_id,
                before_section_id=old_section_id,
                after_section_id=section_id,
                spans=span_deltas,
            )
        )

    for block_id, (section_id, block) in before_blocks.items():
        if block_id in after_blocks:
            continue
        deltas.append(
            BlockDelta(
                status="removed",
                block_id=block_id,
                before_section_id=section_id,
                spans=[
                    SpanDelta(
                        status="removed",
                        span_id=span.id,
                        before_text=span.text,
                        anchor_ids=[a.anchor_id for a in span.citation_anchors],
                    )
                    for span in block.spans
                ],
            )
        )

    return deltas


def _spans(old_block, new_block, before_spans) -> list[SpanDelta]:  # noqa: ANN001
    old_ids = [span.id for span in old_block.spans]
    new_ids = [span.id for span in new_block.spans]
    deltas: list[SpanDelta] = []

    for span in new_block.spans:
        previous = before_spans.get(span.id)
        if previous is None:
            deltas.append(
                SpanDelta(
                    status="added",
                    span_id=span.id,
                    after_text=span.text,
                    anchor_ids=[a.anchor_id for a in span.citation_anchors],
                )
            )
        elif previous.text != span.text or [a.anchor_id for a in previous.citation_anchors] != [
            a.anchor_id for a in span.citation_anchors
        ]:
            deltas.append(
                SpanDelta(
                    status="modified",
                    span_id=span.id,
                    before_text=previous.text,
                    after_text=span.text,
                    anchor_ids=[a.anchor_id for a in span.citation_anchors],
                )
            )

    for span in old_block.spans:
        if span.id not in new_ids:
            deltas.append(
                SpanDelta(
                    status="removed",
                    span_id=span.id,
                    before_text=span.text,
                    anchor_ids=[a.anchor_id for a in span.citation_anchors],
                )
            )

    if old_ids == new_ids and not deltas:
        return []
    return deltas


__all__ = [
    "AnchorDelta",
    "BlockDelta",
    "CitationLedger",
    "SpanDelta",
    "StructuralDiff",
    "build_diff",
]
