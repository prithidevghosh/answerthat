"""IR traversal, validation, diff, and copy-on-write versioning."""

from __future__ import annotations

from collections import Counter

import pytest

import app.ir.diff as ir_diff
from app.core.contracts import Document
from app.core.errors import IRVersionConflict
from app.ir import traversal as tv
from app.ir.builder import DocumentBuilder
from app.ir.ids import ANCHOR, SPAN, new_id, prefix_of, stable_id
from app.ir.store import InMemoryDocumentStore

# ---------------------------------------------------------------- ids


def test_stable_id_is_deterministic_and_prefixed() -> None:
    a = stable_id(SPAN, "doc1", 0, 1, 2)
    assert a == stable_id(SPAN, "doc1", 0, 1, 2)
    assert a != stable_id(SPAN, "doc1", 0, 1, 3)
    assert prefix_of(a) == SPAN


def test_new_id_is_unique() -> None:
    assert new_id(ANCHOR) != new_id(ANCHOR)


def test_rebuilding_the_same_document_reproduces_every_id(sample_doc: Document) -> None:
    """Re-parsing a PDF must not look like a wholesale rewrite of the document."""
    rebuilt = _build_sample()
    assert tv.span_ids(rebuilt) == tv.span_ids(sample_doc)
    assert tv.anchor_ids(rebuilt) == tv.anchor_ids(sample_doc)


def _build_sample() -> Document:
    b = DocumentBuilder("doc_test0001", title="Attention Considered Expensive")
    intro = b.section("Introduction", level=1)
    p1, s1 = intro.paragraph(
        "Transformer models dominate sequence modelling. Their quadratic attention cost "
        "has motivated a long line of efficiency work."
    )
    p1.anchor(s1, source_ids=["src_vaswani"], offset_in_span=46, original_marker_text="[1]")
    p1.anchor(s1, source_ids=["src_tay", "src_child"], offset_in_span=123, original_marker_text="[2, 3]")
    intro.paragraph("We revisit that assumption on modern hardware.")
    method = b.section("Method", level=1)
    p3, s3 = method.paragraph("We follow the standard training recipe with no modifications.")
    p3.anchor(s3, source_ids=["src_vaswani"], offset_in_span=60, original_marker_text="[1]")
    method.placeholder("figure", "Figure 1: Throughput against sequence length.")
    b.quarantine("Smith, J. mumble mumble 20??, pp. ??-??", "parse_failed", page=9)
    return b.build()


# ---------------------------------------------------------------- traversal


def test_text_lives_only_in_spans(sample_doc: Document) -> None:
    """ADR-004. A Block has no text attribute to accidentally write to."""
    block = next(b for _, b in tv.iter_blocks(sample_doc))
    assert not hasattr(block, "text")
    assert tv.text_of_block(block).startswith("Transformer models")


def test_anchors_are_nodes_not_characters(sample_doc: Document) -> None:
    """No marker text appears inside the span string; anchors carry it separately."""
    for ref in tv.iter_spans(sample_doc):
        assert "[1]" not in ref.span.text
        assert "[2, 3]" not in ref.span.text
    markers = [r.anchor.original_marker_text for r in tv.iter_anchors(sample_doc)]
    assert markers == ["[1]", "[2, 3]", "[1]"]


def test_source_id_multiset_counts_multiplicity(sample_doc: Document) -> None:
    assert tv.source_id_multiset(sample_doc) == Counter(
        {"src_vaswani": 2, "src_tay": 1, "src_child": 1}
    )


def test_structural_counts(sample_doc: Document) -> None:
    assert tv.section_titles(sample_doc) == ["Introduction", "Method"]
    assert tv.paragraph_count(sample_doc) == 3
    assert tv.block_count(sample_doc) == 4  # three paragraphs + one figure placeholder


def test_find_helpers(sample_doc: Document) -> None:
    anchor_id = tv.anchor_ids(sample_doc)[0]
    ref = tv.find_anchor(sample_doc, anchor_id)
    assert ref is not None
    assert ref.section.title == "Introduction"
    assert tv.find_anchor(sample_doc, "anc_nope") is None
    assert tv.find_span(sample_doc, "spn_nope") is None


# ---------------------------------------------------------------- validation


def test_clean_document_validates(sample_doc: Document, known_source_ids: set[str]) -> None:
    assert tv.validate(sample_doc, known_source_ids) == []


def test_unknown_source_id_is_reported(sample_doc: Document) -> None:
    """HR-1 seen from the IR side: an anchor pointing at nothing is a defect."""
    problems = tv.validate(sample_doc, {"src_vaswani"})
    codes = {p.code for p in problems}
    assert codes == {"unknown_source_id"}
    assert any("src_tay" in p.message for p in problems)


def test_anchor_offset_outside_span_is_reported(sample_doc: Document) -> None:
    span = next(tv.iter_spans(sample_doc)).span
    span.citation_anchors[0].offset_in_span = len(span.text) + 5
    assert any(p.code == "anchor_out_of_range" for p in tv.validate(sample_doc))


def test_duplicate_ids_are_reported(sample_doc: Document) -> None:
    spans = [r.span for r in tv.iter_spans(sample_doc)]
    spans[1].id = spans[0].id
    assert any(p.code == "duplicate_id" for p in tv.validate(sample_doc))


def test_placeholder_without_caption_is_reported() -> None:
    """ADR-008: a placeholder the user cannot see is an invisible scope cut."""
    b = DocumentBuilder("doc_x")
    section = b.section("Results")
    section.block("table")  # no caption
    assert any(p.code == "placeholder_without_caption" for p in tv.validate(b.build()))


def test_validate_never_raises_on_a_broken_document() -> None:
    """Callers choose between 'what's wrong' and 'walk what's there'; neither explodes."""
    b = DocumentBuilder("doc_y")
    b.section("Only")
    doc = b.build()
    doc.sections[0].level = 0
    assert [p.code for p in tv.validate(doc)] == ["bad_level"]


# ---------------------------------------------------------------- diff


def test_diff_of_identical_documents_is_empty(sample_doc: Document) -> None:
    assert ir_diff.diff(sample_doc, sample_doc.model_copy(deep=True)).is_empty


def test_diff_detects_shrinking_citation_multiset(sample_doc: Document) -> None:
    after = sample_doc.model_copy(deep=True)
    # Remove one of the two src_vaswani citations.
    for ref in tv.iter_spans(after):
        if ref.span.citation_anchors:
            ref.span.citation_anchors.pop(0)
            break
    d = ir_diff.diff(sample_doc, after)
    assert d.citation_multiset_shrank
    assert d.removed_sources == Counter({"src_vaswani": 1})
    assert len(d.removed_anchor_ids) == 1


def test_diff_distinguishes_a_moved_anchor_from_a_dropped_one(sample_doc: Document) -> None:
    """ADR-013 reattachment moves anchors. That must not read as citation loss."""
    after = sample_doc.model_copy(deep=True)
    anchor = next(tv.iter_anchors(after)).anchor
    anchor.offset_in_span = 3
    d = ir_diff.diff(sample_doc, after)
    assert d.moved_anchor_ids == [anchor.anchor_id]
    assert d.removed_anchor_ids == []
    assert not d.citation_multiset_shrank


def test_diff_reports_changed_span_text(sample_doc: Document) -> None:
    after = sample_doc.model_copy(deep=True)
    ref = next(tv.iter_spans(after))
    ref.span.text = "Shortened."
    d = ir_diff.diff(sample_doc, after)
    assert d.changed_span_ids == [ref.span.id]
    assert d.added_span_ids == [] and d.removed_span_ids == []


# ---------------------------------------------------------------- store


async def test_create_then_head(sample_doc: Document) -> None:
    store = InMemoryDocumentStore()
    created = await store.create(sample_doc)
    assert created.version == 1
    head = await store.head(sample_doc.doc_id)
    assert head is not None and head.version == 1


async def test_commit_increments_version_and_keeps_the_old_one(sample_doc: Document) -> None:
    store = InMemoryDocumentStore()
    await store.create(sample_doc)
    edited = sample_doc.model_copy(deep=True)
    next(tv.iter_spans(edited)).span.text = "Rewritten."
    v2 = await store.commit(edited, parent_version=1, label="shorten intro")
    assert v2.version == 2

    v1 = await store.get(sample_doc.doc_id, 1)
    assert v1 is not None
    assert next(tv.iter_spans(v1)).span.text.startswith("Transformer models")


async def test_store_snapshots_are_copies_not_references(sample_doc: Document) -> None:
    """Copy-on-write: mutating the caller's object must not rewrite history."""
    store = InMemoryDocumentStore()
    await store.create(sample_doc)
    next(tv.iter_spans(sample_doc)).span.text = "MUTATED AFTER STORING"
    stored = await store.get(sample_doc.doc_id, 1)
    assert stored is not None
    assert "MUTATED" not in next(tv.iter_spans(stored)).span.text


async def test_commit_against_a_stale_parent_is_rejected(sample_doc: Document) -> None:
    store = InMemoryDocumentStore()
    await store.create(sample_doc)
    await store.commit(sample_doc.model_copy(deep=True), parent_version=1, label="a")
    with pytest.raises(IRVersionConflict) as exc:
        await store.commit(sample_doc.model_copy(deep=True), parent_version=1, label="b")
    assert "version 2" in str(exc.value)


async def test_every_version_is_revertible_and_revert_appends(sample_doc: Document) -> None:
    """CP-6: every version is revertible — and reverting is itself revertible."""
    store = InMemoryDocumentStore()
    await store.create(sample_doc)
    edited = sample_doc.model_copy(deep=True)
    next(tv.iter_spans(edited)).span.text = "Rewritten."
    await store.commit(edited, parent_version=1, label="rewrite")

    reverted = await store.revert(sample_doc.doc_id, 1)
    assert reverted.version == 3
    assert next(tv.iter_spans(reverted)).span.text.startswith("Transformer models")

    history = await store.history(sample_doc.doc_id)
    assert [h.version for h in history] == [1, 2, 3]
    assert history[2].label == "revert to v1"
    # v2 still exists: the user can undo their undo.
    v2 = await store.get(sample_doc.doc_id, 2)
    assert v2 is not None and next(tv.iter_spans(v2)).span.text == "Rewritten."


async def test_commit_before_create_is_an_error(sample_doc: Document) -> None:
    store = InMemoryDocumentStore()
    with pytest.raises(IRVersionConflict):
        await store.commit(sample_doc, parent_version=1, label="x")


async def test_revert_to_a_missing_version_is_an_error(sample_doc: Document) -> None:
    store = InMemoryDocumentStore()
    await store.create(sample_doc)
    with pytest.raises(IRVersionConflict):
        await store.revert(sample_doc.doc_id, 99)


async def test_quarantine_survives_persistence(sample_doc: Document) -> None:
    """HR-3: a reference we could not parse is not allowed to evaporate on save."""
    store = InMemoryDocumentStore()
    await store.create(sample_doc)
    head = await store.head(sample_doc.doc_id)
    assert head is not None
    assert head.quarantine[0].raw == "Smith, J. mumble mumble 20??, pp. ??-??"
    assert head.quarantine[0].reason == "parse_failed"
