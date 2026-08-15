"""Structural diff between two IR versions.

The audit view and the invariant kernel both need to answer "what actually changed",
and neither can answer it from a text diff. This computes the change in terms the
guarantees are stated in: which anchors appeared or vanished, and how the `source_id`
multiset moved.

Note what this deliberately does *not* do: it does not decide whether a change is
acceptable. That is the kernel's job (ADR-007), and the kernel owns the thresholds. This
module reports; it does not judge.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.core.contracts import Document
from app.ir.traversal import iter_anchors, iter_spans, source_id_multiset

__all__ = ["IRDiff", "diff"]


@dataclass(frozen=True)
class IRDiff:
    added_span_ids: list[str] = field(default_factory=list)
    removed_span_ids: list[str] = field(default_factory=list)
    changed_span_ids: list[str] = field(default_factory=list)

    added_anchor_ids: list[str] = field(default_factory=list)
    removed_anchor_ids: list[str] = field(default_factory=list)
    moved_anchor_ids: list[str] = field(default_factory=list)

    added_sources: Counter[str] = field(default_factory=Counter)
    removed_sources: Counter[str] = field(default_factory=Counter)

    @property
    def citation_multiset_shrank(self) -> bool:
        """HR-5's tripwire: did any source lose an occurrence?

        True here means the change is only legal if the user explicitly approved a
        removal. The kernel makes that call; this just states the fact.
        """
        return bool(self.removed_sources)

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.added_span_ids, self.removed_span_ids, self.changed_span_ids,
                self.added_anchor_ids, self.removed_anchor_ids, self.moved_anchor_ids,
                self.added_sources, self.removed_sources,
            )
        )


def diff(before: Document, after: Document) -> IRDiff:
    before_spans = {r.span.id: r for r in iter_spans(before)}
    after_spans = {r.span.id: r for r in iter_spans(after)}
    before_anchors = {r.anchor.anchor_id: r for r in iter_anchors(before)}
    after_anchors = {r.anchor.anchor_id: r for r in iter_anchors(after)}

    changed = [
        sid
        for sid in before_spans.keys() & after_spans.keys()
        if before_spans[sid].span.text != after_spans[sid].span.text
    ]

    # "Moved" means the anchor survived but now lives in a different span or at a
    # different offset — the detach/reattach path (ADR-013) produces exactly this, and
    # it is emphatically not the same event as a drop.
    moved = [
        aid
        for aid in before_anchors.keys() & after_anchors.keys()
        if (before_anchors[aid].span.id, before_anchors[aid].anchor.offset_in_span)
        != (after_anchors[aid].span.id, after_anchors[aid].anchor.offset_in_span)
    ]

    before_sources = source_id_multiset(before)
    after_sources = source_id_multiset(after)

    return IRDiff(
        added_span_ids=sorted(after_spans.keys() - before_spans.keys()),
        removed_span_ids=sorted(before_spans.keys() - after_spans.keys()),
        changed_span_ids=sorted(changed),
        added_anchor_ids=sorted(after_anchors.keys() - before_anchors.keys()),
        removed_anchor_ids=sorted(before_anchors.keys() - after_anchors.keys()),
        moved_anchor_ids=sorted(moved),
        added_sources=after_sources - before_sources,
        removed_sources=before_sources - after_sources,
    )
