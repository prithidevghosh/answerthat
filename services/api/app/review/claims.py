"""Claim extraction — the unit of review is an atomic claim, not a section (ADR-005).

Keyword search over section text is the failure mode this whole design exists to avoid:
it returns papers that are topically adjacent rather than the work the author should
have cited. So the pipeline starts by decomposing sections into **atomic, citable
assertions**, and everything downstream is scoped to one claim.

Two mechanical properties, both deliberate:

* **The model returns character offsets, not text.** Claim text is sliced out of the
  span in code, so a claim is always a verbatim substring of the paper. The model also
  echoes what it thinks it selected; if that echo does not match the slice, its offsets
  were wrong and the claim is dropped rather than silently mis-anchored. Same principle
  as ADR-003's repair tier: make the honesty property mechanical, not prompted.
* **`anchor_ids` are computed, never emitted.** An anchor belongs to a claim when its
  `offset_in_span` falls inside the claim's range. The model never sees an anchor id, so
  it cannot attach a claim to a citation that isn't there.

`citability` is the streaming order (ADR-014): a bald empirical assertion scores high,
"In this section we describe our method" scores zero, so the most consequential findings
reach the user first.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from app.core.contracts import Claim, Document, LLMRole, Section, Span
from app.review.llm import ReviewLLM, object_schema
from app.review.prompts import CLAIM_EXTRACTION_SYSTEM

__all__ = [
    "ClaimExtractor",
    "CLAIM_EXTRACTION_SYSTEM",
    "normalize_for_compare",
    "get_claim_extractor",
]

_WS = re.compile(r"\s+")


def normalize_for_compare(text: str) -> str:
    """NFKC + collapsed whitespace + unified quotes/dashes, for equality checks only."""
    folded = unicodedata.normalize("NFKC", text or "")
    for fancy, plain in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                         ("–", "-"), ("—", "-"), ("−", "-"), (" ", " ")):
        folded = folded.replace(fancy, plain)
    return _WS.sub(" ", folded).strip()


CLAIM_SCHEMA = object_schema(
    {
        "claims": {
            "type": "array",
            "items": object_schema(
                {
                    "span_id": {"type": "string", "description": "The span this claim came from"},
                    "char_start": {"type": "integer", "description": "0-indexed start offset"},
                    "char_end": {"type": "integer", "description": "End offset, exclusive"},
                    "quote": {
                        "type": "string",
                        "description": "The exact substring those offsets select",
                    },
                    "citability": {"type": "number", "description": "0.0 to 1.0"},
                }
            ),
        }
    }
)


class ClaimExtractor:
    """Turns a `Document` (or a subset of it) into claims ordered by citability."""

    def __init__(self, *, llm: ReviewLLM, min_citability: float | None = None) -> None:
        self.llm = llm
        # CITABILITY_MIN (ADR-024). Below it, a "claim" is the authors describing their
        # own paper, and reviewing it against the literature produces noise rather than
        # findings. Read from config rather than inlined: T1 sweeps this against the
        # golden set, which is only possible if it has a name.
        if min_citability is None:
            from app.core.config import get_settings  # noqa: PLC0415

            min_citability = get_settings().citability_min
        self.min_citability = min_citability
        self.discarded_offset_mismatch = 0
        self.discarded_below_threshold = 0

    async def extract(self, document: Document, target_ids: list[str]) -> list[Claim]:
        """Extract claims from the sections/blocks/spans named by `target_ids`.

        An empty `target_ids` means the whole document — full-paper review is the
        default, because partial coverage presented as a review is the dishonesty HR-3
        exists to prevent (ADR-014).
        """
        selected = _select_spans(document, set(target_ids))
        claims: list[Claim] = []
        # One call per section rather than per span: spans in a section share context,
        # and per-span calls would multiply latency for no gain in precision.
        for section_title, spans in selected:
            if not spans:
                continue
            claims.extend(
                await self._extract_from_spans(section_title, spans, doc_id=document.doc_id)
            )

        # Descending citability *is* the streaming order (ADR-014). Ties break on
        # claim_id so the order is stable across runs and the demo is reproducible.
        claims.sort(key=lambda c: (-c.citability, c.claim_id))
        return claims

    async def _extract_from_spans(
        self, section_title: str, spans: list[Span], *, doc_id: str = ""
    ) -> list[Claim]:
        by_id = {span.id: span for span in spans}
        prompt = _build_prompt(section_title, spans)
        payload = await self.llm.complete_json(
            role=LLMRole.CLAIM_EXTRACTION,
            system=CLAIM_EXTRACTION_SYSTEM,
            prompt=prompt,
            schema=CLAIM_SCHEMA,
            doc_id=doc_id,
        )

        claims: list[Claim] = []
        for raw in payload.get("claims") or []:
            claim = self._materialise(raw, by_id)
            if claim is not None:
                claims.append(claim)
        return claims

    def _materialise(self, raw: dict[str, Any], by_id: dict[str, Span]) -> Claim | None:
        span = by_id.get(str(raw.get("span_id", "")))
        if span is None:
            self.discarded_offset_mismatch += 1
            return None

        start, end = raw.get("char_start"), raw.get("char_end")
        if not isinstance(start, int) or not isinstance(end, int):
            self.discarded_offset_mismatch += 1
            return None
        start = max(0, start)
        end = min(len(span.text), end)
        if end - start < 10:
            # Too short to be an assertion; almost always a mis-parsed offset.
            self.discarded_offset_mismatch += 1
            return None

        # The claim text is sliced from the paper, never taken from the model.
        text = span.text[start:end].strip()
        echoed = str(raw.get("quote") or "")
        if normalize_for_compare(echoed) != normalize_for_compare(text):
            # The model's own quote disagrees with its offsets, so the offsets are
            # unreliable and the claim would be anchored to the wrong sentence.
            self.discarded_offset_mismatch += 1
            return None

        citability = float(raw.get("citability") or 0.0)
        if citability < self.min_citability:
            self.discarded_below_threshold += 1
            return None

        return Claim(
            claim_id=_claim_id(span.id, start, end),
            text=text,
            span_id=span.id,
            anchor_ids=_anchors_in_range(span, start, end),
            citability=min(1.0, max(0.0, citability)),
        )

    def snapshot(self) -> dict[str, int]:
        return {
            "discarded_offset_mismatch": self.discarded_offset_mismatch,
            "discarded_below_threshold": self.discarded_below_threshold,
        }


def _anchors_in_range(span: Span, start: int, end: int) -> list[str]:
    """Anchors whose position falls inside the claim. Computed, never model-supplied."""
    return [
        anchor.anchor_id
        for anchor in span.citation_anchors
        if start <= anchor.offset_in_span <= end
    ]


def _claim_id(span_id: str, start: int, end: int) -> str:
    digest = hashlib.sha256(f"{span_id}:{start}:{end}".encode()).hexdigest()[:16]
    return f"clm_{digest}"


def _select_spans(document: Document, target_ids: set[str]) -> list[tuple[str, list[Span]]]:
    """Resolve `target_ids` against sections, blocks and spans.

    Accepts ids at any level so a caller can say "this section", "this paragraph", or
    "this sentence" without three different entry points. Empty means everything.
    """
    selected: list[tuple[str, list[Span]]] = []
    for section in document.sections:
        spans = _spans_for_section(section, target_ids)
        if spans:
            selected.append((section.title, spans))
    return selected


def _spans_for_section(section: Section, target_ids: set[str]) -> list[Span]:
    section_selected = not target_ids or section.id in target_ids
    spans: list[Span] = []
    for block in sorted(section.blocks, key=lambda b: b.order):
        if block.type != "paragraph":
            # Figures, tables and equations carry captions, not claims (ADR-008).
            continue
        block_selected = section_selected or block.id in target_ids
        for span in block.spans:
            if (block_selected or span.id in target_ids) and span.text.strip():
                spans.append(span)
    return spans


def _build_prompt(section_title: str, spans: list[Span]) -> str:
    lines = [
        f"Section: {section_title}",
        "",
        "Spans (offsets are into each span's text exactly as printed below):",
        "",
    ]
    for span in spans:
        lines.append(f"<span id=\"{span.id}\">")
        lines.append(span.text)
        lines.append("</span>")
        lines.append("")
    return "\n".join(lines)


_extractor: ClaimExtractor | None = None


def get_claim_extractor(settings: Any = None) -> ClaimExtractor:
    """Process-wide claim extractor — the `ClaimExtractor` port B3's edit path calls.

    Defined here rather than in `composition.py` because B3's Interface Request names
    this module path. It shares the one LLM client, so per-role routing, record/replay
    and the token budget stay in one place (ADR-015).
    """
    global _extractor
    if _extractor is None:
        from app.review.composition import get_extractor

        _extractor = get_extractor(settings)
    return _extractor


def reset() -> None:
    """Tests only."""
    global _extractor
    _extractor = None
