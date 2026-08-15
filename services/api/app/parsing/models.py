"""Parse-time structures that the frozen contracts deliberately do not carry.

The important one is `anchor_to_ref`.

`CitationAnchor.source_ids` is a foreign key into `source_store`, and `source_store` only
ever receives records from a provider adapter responding to a real HTTP call (HR-1). At
TEI time nothing has been resolved yet, so there is no `source_id` to put there — and
putting GROBID's local `b12` in that field would create a dangling FK that the kernel
would rightly reject.

So the anchor→reference linkage lives here instead, as a side map, and `source_ids` is
populated later, only for references the arbiter actually resolved. An unresolved
reference therefore leaves its in-text anchors with an empty `source_ids` list, which is
the honest state: the marker exists, we know which reference it points at, and we do not
have a record for it. That visible gap is HR-3 working as intended.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.contracts import ConfidenceTier, Document, ParsedReference

__all__ = ["Coordinate", "OrphanMarker", "ParsedDocument", "TierCounts"]


@dataclass(frozen=True)
class Coordinate:
    """A box on a PDF page, as GROBID reports it in `coords`.

    Retained so the frontend can show the user where a reference or heading came from.
    Coordinates are per-element and there may be several when an element wraps lines.
    """

    page: int
    x: float
    y: float
    width: float
    height: float

    @classmethod
    def parse(cls, raw: str) -> list[Coordinate]:
        """Parse a GROBID `coords` attribute: `page,x,y,w,h` groups joined by `;`.

        A malformed group is skipped rather than raising — coordinates are a display
        nicety, and losing one box must not cost us the reference it belongs to. The
        parse of the reference itself is never this forgiving.
        """
        boxes: list[Coordinate] = []
        for group in raw.split(";"):
            parts = group.split(",")
            if len(parts) != 5:
                continue
            try:
                page, x, y, w, h = (float(p) for p in parts)
            except ValueError:
                continue
            boxes.append(cls(page=int(page), x=x, y=y, width=w, height=h))
        return boxes


@dataclass(frozen=True)
class OrphanMarker:
    """An in-text citation marker whose target reference does not exist.

    Detected, located, and surfaced — never quietly deleted, and never silently
    re-pointed at whichever reference looks closest.
    """

    anchor_id: str
    marker_text: str
    target: str | None
    section_id: str
    span_id: str
    page: int | None = None

    @property
    def reason(self) -> str:
        return "missing target attribute" if not self.target else f"target {self.target!r} not in listBibl"


@dataclass
class TierCounts:
    """The HR-3 invariant, as data.

    `total_detected` is set once, from what GROBID found. The tiers must account for
    every one of them — see `assert_invariant`.
    """

    total_detected: int = 0
    resolved: int = 0
    parsed_unresolved: int = 0
    low_confidence: int = 0
    quarantined: int = 0
    orphan_marker: int = 0

    @property
    def accounted_for(self) -> int:
        """Orphan markers are counted separately: they are markers, not references.

        An orphan marker has no `biblStruct` behind it, so including it here would make
        the sum exceed the number of references GROBID detected and mask a real leak.
        """
        return self.resolved + self.parsed_unresolved + self.low_confidence + self.quarantined

    def assert_invariant(self) -> None:
        if self.accounted_for != self.total_detected:
            raise AssertionError(
                "reference tier invariant violated (HR-3): "
                f"resolved({self.resolved}) + parsed_unresolved({self.parsed_unresolved}) + "
                f"low_confidence({self.low_confidence}) + quarantined({self.quarantined}) = "
                f"{self.accounted_for}, but {self.total_detected} references were detected. "
                f"{self.total_detected - self.accounted_for} reference(s) were dropped somewhere "
                "in the pipeline. No reference is ever dropped."
            )


@dataclass
class ParsedDocument:
    """Everything one PDF produced: the IR, its references, and the links between them."""

    document: Document
    references: list[ParsedReference] = field(default_factory=list)
    # anchor_id -> the reference's ref_id. The linkage GROBID gives us and we never rebuild.
    anchor_to_ref: dict[str, str] = field(default_factory=dict)
    orphan_markers: list[OrphanMarker] = field(default_factory=list)
    # node_id (section/block/span/ref_id) -> PDF boxes
    coordinates: dict[str, list[Coordinate]] = field(default_factory=dict)
    raw_tei: str = ""

    def reference_by_id(self, ref_id: str) -> ParsedReference | None:
        return next((r for r in self.references if r.ref_id == ref_id), None)

    def anchors_for_reference(self, ref_id: str) -> list[str]:
        return [a for a, r in self.anchor_to_ref.items() if r == ref_id]

    def tier_counts(self) -> TierCounts:
        counts = TierCounts(total_detected=len(self.references))
        for reference in self.references:
            match reference.tier:
                case ConfidenceTier.RESOLVED:
                    counts.resolved += 1
                case ConfidenceTier.PARSED_UNRESOLVED:
                    counts.parsed_unresolved += 1
                case ConfidenceTier.LOW_CONFIDENCE:
                    counts.low_confidence += 1
                case ConfidenceTier.QUARANTINED:
                    counts.quarantined += 1
                case ConfidenceTier.ORPHAN_MARKER:
                    # A reference is never in this tier; the tier describes a marker.
                    counts.orphan_marker += 1
                    counts.total_detected -= 1
        counts.orphan_marker += len(self.orphan_markers)
        return counts
