"""Detach → transform → reattach (ADR-013) — the mechanism behind HR-5.

    1. DETACH     pull the anchors out of the target, recording each one's
                  context_fingerprint (an embedding of its host sentence)
    2. TRANSFORM  compress or rewrite the TEXT ONLY
                  ── the model never sees or emits a citation marker ──
    3. REATTACH   score each fingerprint against every new sentence,
                  attach at argmax if >= threshold
    4. SURFACE    any anchor below threshold is NOT dropped — it becomes a
                  user decision: keep here / move to… / remove

Step 2 is the whole trick: you cannot lose a citation you never showed the model. Step 4
converts the residual hard cases from silent deletion into visible choices.

The alternative — asking a model to be careful while rewriting a paragraph containing
`[12]` — makes citation preservation a matter of prompt and luck. This module makes it a
property of the data flow instead.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.agent.kernel import DEFAULT_SIMILARITY_THRESHOLD, ReattachmentRecord
from app.agent.ports import Embedder, TextModel
from app.core.contracts import Block, CitationAnchor, Span

# Sentence boundaries, guarded against the abbreviations academic prose is full of.
_ABBREVIATIONS = (
    "et al.", "i.e.", "e.g.", "cf.", "vs.", "Fig.", "Eq.", "Sec.", "Tab.", "Ref.",
    "approx.", "resp.", "No.", "pp.", "Dr.", "Prof.", "St.", "al.",
)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])[\"')\]]*\s+")

# Residual citation-marker shapes, used only to detect a leak — never to edit prose.
_MARKER_LEAK = re.compile(r"\[\s*\d+(\s*[,–-]\s*\d+)*\s*\]|\\cite[a-zA-Z]*\{")


class MarkerLeakError(RuntimeError):
    """A citation marker reached the boundary of the text model. Loud, never tolerated."""


@dataclass(frozen=True)
class DetachedAnchor:
    """An anchor lifted out of the text, with the context needed to put it back."""

    anchor: CitationAnchor
    origin_span_id: str
    host_sentence: str


@dataclass
class TransformReport:
    """Everything the executor and the kernel need to know about what happened."""

    new_spans: list[Span] = field(default_factory=list)
    derived_spans: dict[str, list[str]] = field(default_factory=dict)
    reattachments: list[ReattachmentRecord] = field(default_factory=list)
    orphaned_anchors: list[CitationAnchor] = field(default_factory=list)
    marker_leaks: list[str] = field(default_factory=list)
    """Citation-marker-shaped strings the text model emitted despite never being shown one.
    They are removed from the prose and reported — a leak is a prompt regression, and
    silently tolerating it is exactly the "best effort" HR-3 prohibits."""

    @property
    def orphaned_anchor_ids(self) -> list[str]:
        return [a.anchor_id for a in self.orphaned_anchors]


# --------------------------------------------------------------------------- text utils


def split_sentences(text: str) -> list[str]:
    """Deterministic sentence split. No model, so a transform is reproducible."""
    if not text.strip():
        return []
    protected = text
    for index, abbreviation in enumerate(_ABBREVIATIONS):
        protected = protected.replace(abbreviation, f"\x00{index}\x00")
    pieces = [p.strip() for p in _SENTENCE_BOUNDARY.split(protected) if p.strip()]
    restored = []
    for piece in pieces:
        for index, abbreviation in enumerate(_ABBREVIATIONS):
            piece = piece.replace(f"\x00{index}\x00", abbreviation)
        restored.append(piece)
    return restored


def host_sentence_for(text: str, offset: int) -> str:
    """The sentence an anchor sits in — the thing we fingerprint."""
    sentences = split_sentences(text)
    if not sentences:
        return text.strip()
    cursor = 0
    for sentence in sentences:
        position = text.find(sentence, cursor)
        if position == -1:
            position = cursor
        end = position + len(sentence)
        if offset <= end:
            return sentence
        cursor = end
    return sentences[-1]


def scrub_markers(text: str, anchors: list[CitationAnchor]) -> str:
    """Remove any literal citation marker still embedded in the prose.

    B1's IR keeps markers on the anchor rather than in `Span.text`, so this is normally a
    no-op. It runs anyway: "the model never sees a citation marker" is a guarantee we
    enforce at the boundary, not an assumption we make about someone else's parser.
    """
    cleaned = text
    for anchor in anchors:
        marker = (anchor.original_marker_text or "").strip()
        if marker:
            cleaned = cleaned.replace(marker, "")
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def strip_marker_leaks(text: str) -> tuple[str, list[str]]:
    leaks = [m.group(0) for m in _MARKER_LEAK.finditer(text)]
    if not leaks:
        return text, []
    cleaned = _MARKER_LEAK.sub("", text)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip(), leaks


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------------------------------- pipeline


class DetachTransformReattach:
    def __init__(
        self,
        embedder: Embedder,
        text_model: TextModel,
        *,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        self._embedder = embedder
        self._text = text_model
        self.threshold = threshold

    # ---- 1. DETACH

    async def detach(self, block: Block) -> tuple[str, list[DetachedAnchor]]:
        """Returns marker-free prose and the anchors lifted out of it."""
        detached: list[DetachedAnchor] = []
        parts: list[str] = []

        for span in block.spans:
            parts.append(scrub_markers(span.text, span.citation_anchors))
            for anchor in span.citation_anchors:
                detached.append(
                    DetachedAnchor(
                        anchor=anchor,
                        origin_span_id=span.id,
                        host_sentence=host_sentence_for(span.text, anchor.offset_in_span),
                    )
                )

        needs_fingerprint = [d for d in detached if not d.anchor.context_fingerprint]
        if needs_fingerprint:
            vectors = await self._embedder.embed([d.host_sentence for d in needs_fingerprint])
            if len(vectors) != len(needs_fingerprint):
                raise RuntimeError(
                    f"embedder returned {len(vectors)} vectors for {len(needs_fingerprint)} sentences"
                )
            for detached_anchor, vector in zip(needs_fingerprint, vectors, strict=True):
                detached_anchor.anchor.context_fingerprint = vector

        return " ".join(p for p in parts if p).strip(), detached

    # ---- 2. TRANSFORM

    async def transform(self, prose: str, *, system: str, instruction: str) -> tuple[str, list[str]]:
        """The only model call in this pipeline. It receives prose with no markers in it,
        and whatever it returns is scrubbed again before anything is attached."""
        if _MARKER_LEAK.search(prose):
            raise MarkerLeakError(
                "detach produced prose that still contains a citation marker; refusing to send it "
                "to the text model (ADR-013 step 2)"
            )
        produced = await self._text.rewrite(system=system, instruction=instruction, text=prose)
        return strip_marker_leaks(produced or "")

    # ---- 3 & 4. REATTACH / SURFACE

    async def reattach(
        self,
        block_id: str,
        new_prose: str,
        detached: list[DetachedAnchor],
    ) -> TransformReport:
        sentences = split_sentences(new_prose)
        report = TransformReport()

        if not sentences:
            # The model returned nothing usable. Every anchor becomes a user decision;
            # none is dropped. The caller's kernel verdict will flag them.
            report.orphaned_anchors = [d.anchor for d in detached]
            report.reattachments = [
                ReattachmentRecord(anchor_id=d.anchor.anchor_id, landed_span_id=None, score=None,
                                   threshold=self.threshold)
                for d in detached
            ]
            return report

        new_spans = [
            Span(id=f"{block_id}::s{index}", text=sentence, citation_anchors=[])
            for index, sentence in enumerate(sentences)
        ]
        by_id = {span.id: span for span in new_spans}

        sentence_vectors = await self._embedder.embed(sentences) if detached else []
        if detached and len(sentence_vectors) != len(sentences):
            raise RuntimeError(
                f"embedder returned {len(sentence_vectors)} vectors for {len(sentences)} sentences"
            )

        for detached_anchor in detached:
            fingerprint = detached_anchor.anchor.context_fingerprint or []
            scores = [cosine(fingerprint, vector) for vector in sentence_vectors]
            best_index = max(range(len(scores)), key=scores.__getitem__) if scores else -1
            best_score = scores[best_index] if best_index >= 0 else 0.0

            if best_index < 0 or best_score < self.threshold:
                report.orphaned_anchors.append(detached_anchor.anchor)
                report.reattachments.append(
                    ReattachmentRecord(
                        anchor_id=detached_anchor.anchor.anchor_id,
                        landed_span_id=None,
                        score=best_score if best_index >= 0 else None,
                        threshold=self.threshold,
                        best_span_id=new_spans[best_index].id if best_index >= 0 else None,
                    )
                )
                continue

            target = new_spans[best_index]
            placed = detached_anchor.anchor.model_copy(
                update={"offset_in_span": len(target.text), "confidence": round(best_score, 4)}
            )
            by_id[target.id].citation_anchors.append(placed)
            report.reattachments.append(
                ReattachmentRecord(
                    anchor_id=placed.anchor_id,
                    landed_span_id=target.id,
                    score=best_score,
                    threshold=self.threshold,
                    best_span_id=target.id,
                )
            )

        report.new_spans = new_spans
        return report

    # ---- the whole sequence

    async def run(
        self,
        block: Block,
        *,
        system: str,
        instruction: str,
    ) -> TransformReport:
        prose, detached = await self.detach(block)
        new_prose, leaks = await self.transform(prose, system=system, instruction=instruction)
        report = await self.reattach(block.id, new_prose, detached)
        report.marker_leaks = leaks
        origin_span_ids = [span.id for span in block.spans]
        report.derived_spans = {span.id: list(origin_span_ids) for span in report.new_spans}
        return report


__all__ = [
    "DetachTransformReattach",
    "DetachedAnchor",
    "MarkerLeakError",
    "TransformReport",
    "cosine",
    "host_sentence_for",
    "scrub_markers",
    "split_sentences",
    "strip_marker_leaks",
]
