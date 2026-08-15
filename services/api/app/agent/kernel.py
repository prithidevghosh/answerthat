"""The invariant kernel.

**Pure code. No LLM call. Ever.** (ADR-007, HR-1, HR-5.)

Everything a model produces passes through `InvariantKernel.evaluate` before it can reach
a document version. The kernel is the enforcement point, not a linter: there is no code
path in `app/agent/` that commits a change without a verdict, and the kernel has no way to
ask a model anything — it holds a `SourceReader` (no `put`) and a `RenderProbe`, and that
is the whole of its collaborators.

REJECT and FLAG are kept sharply separate on purpose. A reject is invalid and is discarded
with its reason handed back to the planner. A flag is valid but uncertain and goes to the
user with the warning attached. Blurring the two is how this class of system either
silently discards good edits or waves through bad ones.

`KernelVerdict.reasons` is never empty for a reject or a flag.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.agent.fragment import (
    Fragment,
    FragmentApplicationError,
    anchors_by_id,
    apply_fragment,
    iter_anchors,
    iter_blocks,
    iter_spans,
    source_multiset,
)
from app.agent.ports import RenderProbe, SourceReader
from app.core.contracts import (
    Document,
    KernelVerdict,
    ProposedChange,
    Verification,
    VerificationLabel,
)

DEFAULT_SIMILARITY_THRESHOLD = 0.82

# Reject codes — one per normative REJECT rule in goal.md Appendix A.
REJECT_UNKNOWN_SOURCE_ID = "unknown_source_id"          # rule 1 (HR-1)
REJECT_CITATION_MULTISET_SHRANK = "citation_multiset_shrank"  # rule 2 (HR-5)
REJECT_UNGROUNDED_NEW_CLAIM = "ungrounded_new_claim"    # rule 3
REJECT_IR_SCHEMA_VIOLATION = "ir_schema_violation"      # rule 4
REJECT_PANDOC_REFUSED = "pandoc_refused"                # rule 5

# Flag codes — one per normative FLAG rule.
FLAG_LOW_CONFIDENCE_REATTACHMENT = "low_confidence_reattachment"  # flag 1
FLAG_ORPHANED_ANCHOR = "orphaned_anchor"                          # flag 2
FLAG_WEAK_VERIFICATION = "weak_verification"                      # flag 3

GROUNDING_LABELS = frozenset({VerificationLabel.SUPPORTS, VerificationLabel.PARTIALLY_SUPPORTS})


class ReattachmentRecord(BaseModel):
    """What happened to one anchor during detach → transform → reattach (ADR-013)."""

    anchor_id: str
    landed_span_id: str | None = None  # None → the anchor found no home
    score: float | None = None
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD
    best_span_id: str | None = None
    """The argmax span regardless of threshold. Kept so that "keep it here" remains an
    option the user can actually take when an anchor falls below the bar — a decision the
    UI can only offer if we remember where the anchor nearly landed."""


class ChangeContext(BaseModel):
    """Evidence the executor hands to the kernel alongside the change.

    None of this is trusted prose — every field is produced by pure executor code from
    facts it observed, and the kernel cross-checks each one against the documents.
    """

    derived_spans: dict[str, list[str]] = Field(default_factory=dict)
    """new/mutated span id → the before-document span ids it was transformed from.

    A span with no derivation is text that came from nowhere, i.e. a newly asserted
    claim, and REJECT rule 3 then demands it carry a supporting anchor. An executor
    cannot dodge this by claiming a derivation: the kernel checks the cited source spans
    actually existed in the before document.
    """

    verifications: dict[str, Verification] = Field(default_factory=dict)
    """anchor_id → B2's verification verdict for that anchor's source against its claim."""

    approved_removals: dict[str, int] = Field(default_factory=dict)
    """source_id → how many occurrences the user explicitly approved removing (HR-5)."""

    reattachments: list[ReattachmentRecord] = Field(default_factory=list)


class InvariantKernel:
    """Pure. Deterministic. Holds no model client and never acquires one."""

    def __init__(
        self,
        sources: SourceReader,
        render_probe: RenderProbe,
        *,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        self._sources = sources
        self._render = render_probe
        self.similarity_threshold = similarity_threshold

    @property
    def sources(self) -> SourceReader:
        """Read-only, by type. There is no `put` on this object and never will be (HR-1)."""
        return self._sources

    # ------------------------------------------------------------------ public API

    def evaluate(
        self,
        *,
        before: Document,
        change: ProposedChange,
        context: ChangeContext | None = None,
    ) -> KernelVerdict:
        context = context or ChangeContext()
        rejects: list[str] = []

        fragment, fragment_error = self._parse_fragment(change)
        if fragment_error is not None:
            return _verdict("reject", [f"{REJECT_IR_SCHEMA_VIOLATION}: {fragment_error}"])

        assert fragment is not None

        # Rule 1 first and unconditionally: an unknown source_id is fabrication, and we
        # refuse to reason any further about a change that contains one.
        rejects += self._check_source_ids(change, fragment)

        after, apply_error = self._apply(before, fragment)
        if apply_error is not None:
            rejects.append(f"{REJECT_IR_SCHEMA_VIOLATION}: {apply_error}")
            return _verdict("reject", rejects)

        assert after is not None

        schema_problems = self._check_schema(after, change, before)
        rejects += schema_problems
        if schema_problems:
            # A malformed document makes the remaining checks meaningless.
            return _verdict("reject", rejects)

        rejects += self._check_citation_multiset(before, after, change, context)
        rejects += self._check_new_claims(before, after, context)
        rejects += self._check_renderable(after)

        if rejects:
            return _verdict("reject", rejects)

        flags, flag_reasons = self._collect_flags(before, after, change, context)
        if flags:
            return KernelVerdict(decision="flag", reasons=flag_reasons, flags=flags)

        return KernelVerdict(decision="accept", reasons=[], flags=[])

    @staticmethod
    def referenced_source_ids(change: ProposedChange) -> list[str]:
        """Every `source_id` REJECT rule 1 will look up, computed without touching the store.

        This exists so a caller can warm B2's source store before calling `evaluate` — its
        sync `has()` answers from an in-process index and raises on an id that was never
        warmed, because reporting "we never looked" as "does not exist" would be a false
        REJECT indistinguishable from a real one (memory.md §5, B2 → B3).

        Warming is the caller's job precisely so that it is not the kernel's: the kernel
        stays pure and synchronous, with no reason to ever await anything.
        """
        try:
            fragment = Fragment.model_validate(change.new_fragment)
        except ValidationError:
            return list(dict.fromkeys(change.new_source_ids))

        ids = list(change.new_source_ids)
        for anchor, _where in _fragment_anchors(fragment):
            ids.extend(anchor.source_ids)
        return list(dict.fromkeys(ids))

    def project(self, before: Document, change: ProposedChange) -> Document:
        """The after-document the kernel judged. Used by the diff builder and by commit,
        so what the user approves is byte-for-byte what the kernel accepted."""
        return apply_fragment(before, Fragment.model_validate(change.new_fragment))

    # ------------------------------------------------------------------ rule 4 (part)

    @staticmethod
    def _parse_fragment(change: ProposedChange) -> tuple[Fragment | None, str | None]:
        try:
            return Fragment.model_validate(change.new_fragment), None
        except ValidationError as exc:
            return None, f"new_fragment is not a valid partial IR ({exc.error_count()} errors): {exc}"

    @staticmethod
    def _apply(before: Document, fragment: Fragment) -> tuple[Document | None, str | None]:
        try:
            return apply_fragment(before, fragment), None
        except FragmentApplicationError as exc:
            return None, str(exc)

    # ------------------------------------------------------------------ REJECT rule 1

    def _check_source_ids(self, change: ProposedChange, fragment: Fragment) -> list[str]:
        """HR-1. Every source_id anywhere in the change must already exist in the
        append-only source_store, which only provider adapters may write to. An LLM
        cannot mint one; it can only reference one B2's adapters already wrote."""
        referenced: dict[str, list[str]] = {}

        for source_id in change.new_source_ids:
            referenced.setdefault(source_id, []).append("new_source_ids")

        for anchor, where in _fragment_anchors(fragment):
            for source_id in anchor.source_ids:
                referenced.setdefault(source_id, []).append(where)

        reasons: list[str] = []
        for source_id in sorted(referenced):
            if not self._sources.has(source_id):
                sites = ", ".join(sorted(set(referenced[source_id])))
                reasons.append(
                    f"{REJECT_UNKNOWN_SOURCE_ID}: source_id {source_id!r} (referenced by {sites}) "
                    f"is not present in source_store. Only a provider adapter may create a source_id; "
                    f"cite a source_id returned by retrieval instead of composing one."
                )
        return reasons

    # ------------------------------------------------------------------ REJECT rule 4

    @staticmethod
    def _check_schema(after: Document, change: ProposedChange, before: Document) -> list[str]:
        reasons: list[str] = []

        try:
            Document.model_validate(after.model_dump())
        except ValidationError as exc:
            reasons.append(f"{REJECT_IR_SCHEMA_VIOLATION}: {exc}")
            return reasons

        seen_section_ids: Counter[str] = Counter(s.id for s in after.sections)
        duplicates = [i for i, n in seen_section_ids.items() if n > 1]
        if duplicates:
            reasons.append(f"{REJECT_IR_SCHEMA_VIOLATION}: duplicate section ids {sorted(duplicates)}")

        block_ids: Counter[str] = Counter(b.id for _s, b in iter_blocks(after))
        duplicates = [i for i, n in block_ids.items() if n > 1]
        if duplicates:
            reasons.append(f"{REJECT_IR_SCHEMA_VIOLATION}: duplicate block ids {sorted(duplicates)}")

        span_ids: Counter[str] = Counter(sp.id for _s, _b, sp in iter_spans(after))
        duplicates = [i for i, n in span_ids.items() if n > 1]
        if duplicates:
            reasons.append(f"{REJECT_IR_SCHEMA_VIOLATION}: duplicate span ids {sorted(duplicates)}")

        anchor_ids: Counter[str] = Counter(a.anchor_id for _sp, a in iter_anchors(after))
        duplicates = [i for i, n in anchor_ids.items() if n > 1]
        if duplicates:
            reasons.append(f"{REJECT_IR_SCHEMA_VIOLATION}: duplicate anchor ids {sorted(duplicates)}")

        for section in after.sections:
            orders = Counter(b.order for b in section.blocks)
            if any(n > 1 for n in orders.values()):
                reasons.append(
                    f"{REJECT_IR_SCHEMA_VIOLATION}: section {section.id!r} has duplicate block orders"
                )

        for span, anchor in iter_anchors(after):
            if not anchor.source_ids:
                reasons.append(
                    f"{REJECT_IR_SCHEMA_VIOLATION}: anchor {anchor.anchor_id!r} carries no source_ids"
                )
            if not 0 <= anchor.offset_in_span <= len(span.text):
                reasons.append(
                    f"{REJECT_IR_SCHEMA_VIOLATION}: anchor {anchor.anchor_id!r} offset "
                    f"{anchor.offset_in_span} is outside span {span.id!r} (len {len(span.text)})"
                )
            if not 0.0 <= anchor.confidence <= 1.0:
                reasons.append(
                    f"{REJECT_IR_SCHEMA_VIOLATION}: anchor {anchor.anchor_id!r} confidence "
                    f"{anchor.confidence} is outside [0, 1]"
                )

        before_anchors = anchors_by_id(before)
        for anchor_id in change.orphaned_anchor_ids:
            if anchor_id not in before_anchors:
                reasons.append(
                    f"{REJECT_IR_SCHEMA_VIOLATION}: orphaned_anchor_ids names {anchor_id!r}, "
                    f"which does not exist in the document being changed"
                )

        return reasons

    # ------------------------------------------------------------------ REJECT rule 2

    def _check_citation_multiset(
        self,
        before: Document,
        after: Document,
        change: ProposedChange,
        context: ChangeContext,
    ) -> list[str]:
        """HR-5. The multiset may not shrink without an approved removal.

        An anchor surfaced for a user decision is *not* a loss: it is held pending that
        decision, so its sources still count. An anchor that is neither in the document
        nor held is a silent deletion, and that is exactly what this rejects.
        """
        reasons: list[str] = []
        before_anchors = anchors_by_id(before)
        after_anchor_ids = {a.anchor_id for _sp, a in iter_anchors(after)}

        held: Counter[str] = Counter()
        for anchor_id in change.orphaned_anchor_ids:
            entry = before_anchors.get(anchor_id)
            if entry is None or anchor_id in after_anchor_ids:
                continue  # unknown id already rejected by rule 4; still-attached ids are counted below
            held.update(entry[1].source_ids)

        # An anchor the transform could not place must be surfaced, not dropped.
        surfaced = set(change.orphaned_anchor_ids)
        for record in context.reattachments:
            unplaced = record.landed_span_id is None
            if unplaced and record.anchor_id not in surfaced | after_anchor_ids:
                reasons.append(
                    f"{REJECT_CITATION_MULTISET_SHRANK}: anchor {record.anchor_id!r} found no home "
                    f"after the transform and was neither reattached nor surfaced for a user "
                    f"decision. An anchor below threshold is raised to the user, never dropped."
                )

        effective_after = source_multiset(after) + held
        shrinkage = source_multiset(before) - effective_after

        for source_id in sorted(shrinkage):
            lost = shrinkage[source_id]
            approved = context.approved_removals.get(source_id, 0)
            if approved < lost:
                reasons.append(
                    f"{REJECT_CITATION_MULTISET_SHRANK}: source_id {source_id!r} loses {lost} "
                    f"occurrence(s) but only {approved} removal(s) were approved by the user. "
                    f"No edit may reduce the citation multiset without an approved removal (HR-5)."
                )
        return reasons

    # ------------------------------------------------------------------ REJECT rule 3

    @staticmethod
    def _check_new_claims(before: Document, after: Document, context: ChangeContext) -> list[str]:
        """A newly asserted claim must carry an anchor verified SUPPORTS/PARTIALLY_SUPPORTS.

        "Newly asserted" is mechanical: a span whose id is new, or whose text changed,
        and which the executor did not declare as derived from spans that really existed
        before. Rewritten text is derived; invented text is not.
        """
        reasons: list[str] = []
        before_spans = {sp.id: sp for _s, _b, sp in iter_spans(before)}

        for _section, _block, span in iter_spans(after):
            previous = before_spans.get(span.id)
            if previous is not None and previous.text == span.text:
                continue  # untouched text asserts nothing new

            derivation = context.derived_spans.get(span.id, [])
            valid_derivation = [src for src in derivation if src in before_spans]
            if derivation and len(valid_derivation) == len(derivation):
                continue  # a transform of text that was already in the document

            if derivation and not valid_derivation:
                reasons.append(
                    f"{REJECT_UNGROUNDED_NEW_CLAIM}: span {span.id!r} claims derivation from "
                    f"{sorted(set(derivation))}, none of which exist in the document being changed."
                )
                continue

            grounded = any(
                (context.verifications.get(anchor.anchor_id) or _NO_VERIFICATION).label in GROUNDING_LABELS
                for anchor in span.citation_anchors
            )
            if not grounded:
                reasons.append(
                    f"{REJECT_UNGROUNDED_NEW_CLAIM}: span {span.id!r} asserts new text with no anchor "
                    f"carrying a SUPPORTS or PARTIALLY_SUPPORTS verification. New claims must be "
                    f"grounded in a verified source, or derived from text already in the document."
                )
        return reasons

    # ------------------------------------------------------------------ REJECT rule 5

    def _check_renderable(self, after: Document) -> list[str]:
        ok, reason = self._render.can_render(after)
        if ok:
            return []
        return [
            f"{REJECT_PANDOC_REFUSED}: Pandoc refused to render the resulting document: "
            f"{reason or 'no reason reported'}"
        ]

    # ------------------------------------------------------------------ FLAGS

    def _collect_flags(
        self,
        before: Document,
        after: Document,
        change: ProposedChange,
        context: ChangeContext,
    ) -> tuple[list[str], list[str]]:
        flags: list[str] = []
        reasons: list[str] = []

        for record in context.reattachments:
            threshold = record.threshold or self.similarity_threshold
            if record.landed_span_id is not None and record.score is not None and record.score < threshold:
                flags.append(FLAG_LOW_CONFIDENCE_REATTACHMENT)
                reasons.append(
                    f"{FLAG_LOW_CONFIDENCE_REATTACHMENT}: anchor {record.anchor_id!r} reattached to span "
                    f"{record.landed_span_id!r} at similarity {record.score:.3f}, below the "
                    f"{threshold:.2f} threshold. Check that it sits with the right sentence."
                )

        before_anchors = anchors_by_id(before)
        for anchor_id in change.orphaned_anchor_ids:
            entry = before_anchors.get(anchor_id)
            marker = entry[1].original_marker_text if entry else None
            flags.append(FLAG_ORPHANED_ANCHOR)
            reasons.append(
                f"{FLAG_ORPHANED_ANCHOR}: anchor {anchor_id!r}"
                + (f" ({marker})" if marker else "")
                + " found no home after this change and is held for your decision: "
                + "keep it where it was, move it to another sentence, or remove it."
            )

        after_anchors = {a.anchor_id: (sp, a) for sp, a in iter_anchors(after)}
        for anchor_id in sorted(after_anchors):
            verification = context.verifications.get(anchor_id)
            if verification is None or verification.label == VerificationLabel.SUPPORTS:
                continue
            flags.append(FLAG_WEAK_VERIFICATION)
            reasons.append(
                f"{FLAG_WEAK_VERIFICATION}: anchor {anchor_id!r} cites a source verified as "
                f"'{verification.label.value}', which is weaker than 'supports'"
                + (f" (abstract source: {verification.abstract_source.value})" if verification else "")
                + "."
            )

        return flags, reasons


class _NoVerification:
    label = None


_NO_VERIFICATION = _NoVerification()


def _fragment_anchors(fragment: Fragment):
    """Every citation anchor carried anywhere in a fragment, with a locator string."""
    for span in fragment.replace_spans:
        for anchor in span.citation_anchors:
            yield anchor, f"replace_spans[{span.id}]"
    for block in fragment.replace_blocks:
        for span in block.spans:
            for anchor in span.citation_anchors:
                yield anchor, f"replace_blocks[{block.id}].spans[{span.id}]"
    for section in fragment.replace_sections:
        for block in section.blocks:
            for span in block.spans:
                for anchor in span.citation_anchors:
                    yield anchor, f"replace_sections[{section.id}]…spans[{span.id}]"
    for insertion in fragment.insert_blocks:
        for span in insertion.block.spans:
            for anchor in span.citation_anchors:
                yield anchor, f"insert_blocks[{insertion.block.id}].spans[{span.id}]"


def _verdict(decision: Literal["reject", "flag"], reasons: list[str]) -> KernelVerdict:
    if not reasons:
        raise AssertionError("KernelVerdict.reasons is never empty for a reject or a flag")
    return KernelVerdict(decision=decision, reasons=reasons, flags=[])


__all__ = [
    "ChangeContext",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "FLAG_LOW_CONFIDENCE_REATTACHMENT",
    "FLAG_ORPHANED_ANCHOR",
    "FLAG_WEAK_VERIFICATION",
    "InvariantKernel",
    "REJECT_CITATION_MULTISET_SHRANK",
    "REJECT_IR_SCHEMA_VIOLATION",
    "REJECT_PANDOC_REFUSED",
    "REJECT_UNGROUNDED_NEW_CLAIM",
    "REJECT_UNKNOWN_SOURCE_ID",
    "ReattachmentRecord",
]
