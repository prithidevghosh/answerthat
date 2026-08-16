"""TEI → IR, and biblStruct → CSL-JSON.

The four CSL cases called out in memory.md §3 each get their own test, because each of
them fails silently: a wrong container-title or a mangled particle produces a plausible
reference that is simply not the one in the paper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.contracts import ConfidenceTier
from app.core.errors import GrobidParseError
from app.ir import traversal as tv
from app.parsing.csl import parse_confidence_for
from app.parsing.references import extract_references
from app.parsing.tei import parse_tei, tei_to_ir

FIXTURES = Path(__file__).parent / "fixtures"
THRESHOLD = 0.75


@pytest.fixture
def tei_xml() -> str:
    return (FIXTURES / "sample.tei.xml").read_text(encoding="utf-8")


@pytest.fixture
def parsed(tei_xml: str):
    return tei_to_ir(tei_xml, doc_id="doc_fixture")


@pytest.fixture
def references(tei_xml: str):
    return extract_references(tei_xml, threshold=THRESHOLD)


# ---------------------------------------------------------------- structure


def test_title_is_extracted(parsed) -> None:
    assert parsed.document.metadata.title == "Revisiting Quadratic Attention on Modern Hardware"


def test_abstract_becomes_a_section(parsed) -> None:
    """The abstract lives in the TEI header, not the body. Dropping it silently
    shortens every document we ingest."""
    assert tv.section_titles(parsed.document)[0] == "Abstract"


def test_sections_and_levels(parsed) -> None:
    titles = tv.section_titles(parsed.document)
    assert titles == ["Abstract", "Introduction", "Experimental Setup"]
    levels = {s.title: s.level for s in parsed.document.sections}
    assert levels["Introduction"] == 1
    assert levels["Experimental Setup"] == 2  # GROBID's @n="2.1"


def test_floats_land_in_a_real_section_not_a_dump_at_the_end(parsed) -> None:
    """Floats used to be swept into a trailing "Figures and Tables" section.

    That moved every figure in the paper to the back, which is not what the paper looked
    like. GROBID hoists them out of the flow, but their coordinates say which page they
    were printed on, so they go back to the section being typeset there.
    """
    titles = tv.section_titles(parsed.document)
    assert "Figures and Tables" not in titles

    by_title = {s.title: s for s in parsed.document.sections}
    assert [b.type for b in by_title["Introduction"].blocks] == ["paragraph", "paragraph", "figure"]
    assert [b.type for b in by_title["Experimental Setup"].blocks] == [
        "paragraph",
        "equation",
        "table",
    ]


def test_no_float_is_dropped_on_the_way_in(parsed) -> None:
    """Placement is a judgement call; keeping the block is not."""
    kinds = [b.type for _, b in tv.iter_blocks(parsed.document)]
    assert kinds.count("figure") == 1
    assert kinds.count("table") == 1
    assert kinds.count("equation") == 1


def test_a_span_is_a_sentence(parsed) -> None:
    """segmentSentences=1 gives <s> elements; reattachment and claims both want them."""
    intro = next(s for s in parsed.document.sections if s.title == "Introduction")
    first_paragraph = intro.blocks[0]
    assert len(first_paragraph.spans) == 3
    assert first_paragraph.spans[0].text.startswith("Transformer models dominate")


def test_marker_text_is_not_in_the_span_text(parsed) -> None:
    """ADR-004: anchors are nodes. The characters '[1]' are on the anchor, not the string."""
    for ref in tv.iter_spans(parsed.document):
        assert "[1]" not in ref.span.text
        assert "[42]" not in ref.span.text
    markers = {r.anchor.original_marker_text for r in tv.iter_anchors(parsed.document)}
    assert {"[1]", "[2]", "[3]", "[42]"} <= markers


def test_anchor_offsets_are_inside_their_spans(parsed) -> None:
    assert [p for p in tv.validate(parsed.document) if p.code == "anchor_out_of_range"] == []


def test_placeholders_carry_captions(parsed) -> None:
    """ADR-008 — and a placeholder without a caption is a defect, not a shrug."""
    blocks = {b.type: b for _, b in tv.iter_blocks(parsed.document)}
    assert blocks["figure"].placeholder_caption == "Figure 1 Throughput against sequence length on an H100."
    assert blocks["table"].placeholder_caption == "Table 1 Ablation over head count."
    assert blocks["equation"].placeholder_caption.startswith("A(Q, K, V )")
    assert [p for p in tv.validate(parsed.document) if p.code == "placeholder_without_caption"] == []


def test_pdf_coordinates_are_retained(parsed) -> None:
    """The frontend shows the user where each thing came from."""
    assert parsed.coordinates, "no coordinates retained at all"
    boxes = parsed.coordinates["b0"]
    assert boxes[0].page == 5
    assert boxes[0].x == pytest.approx(72.0)


# ---------------------------------------------------------------- the GROBID linkage


def test_anchors_link_to_references_by_grobid_target(parsed) -> None:
    """`<ref target="#b12">` → `<biblStruct xml:id="b12">`. We read this, never rebuild it."""
    assert set(parsed.anchor_to_ref.values()) == {"b0", "b1", "b2"}
    # [1] appears twice in the body — introduction and setup — plus nothing else.
    b0_anchors = parsed.anchors_for_reference("b0")
    assert len(b0_anchors) == 2


def test_anchors_start_with_no_source_ids(parsed) -> None:
    """HR-1: source_id is a FK into source_store, and nothing has been resolved yet.
    Putting GROBID's local 'b12' here would be a dangling foreign key."""
    assert all(r.anchor.source_ids == [] for r in tv.iter_anchors(parsed.document))


def test_orphan_marker_is_detected_and_located(parsed) -> None:
    """`[42]` points at #b99, which does not exist in listBibl."""
    assert len(parsed.orphan_markers) == 1
    orphan = parsed.orphan_markers[0]
    assert orphan.marker_text == "[42]"
    assert orphan.target == "#b99"
    assert orphan.section_id and orphan.span_id
    assert "not in listBibl" in orphan.reason
    # It is not silently dropped from the document either.
    assert orphan.anchor_id in tv.anchor_ids(parsed.document)


def test_orphan_marker_reaches_the_quarantine(parsed) -> None:
    entries = [q for q in parsed.document.quarantine if q.reason == "orphan_marker"]
    assert [q.raw for q in entries] == ["[42]"]


# ---------------------------------------------------------------- CSL: the four cases


def test_case_1_name_particles(references) -> None:
    """'van der Berg' is family Berg with a non-dropping particle."""
    author = references[1].csl["author"][0]
    assert author == {"family": "Berg", "given": "Jan", "non-dropping-particle": "van der"}


def test_case_2_analytic_vs_monogr(references) -> None:
    """With an analytic, monogr is the container. Without one, monogr IS the work."""
    article = references[1].csl
    assert article["title"] == "Efficient Transformers: A Survey"
    assert article["container-title"] == "ACM Computing Surveys"
    assert article["type"] == "article-journal"

    book = references[2].csl
    assert book["title"] == "The Elements of Statistical Learning"
    assert "container-title" not in book, "a standalone book has no container"
    assert book["type"] == "book"


def test_case_2b_conference_paper_is_not_a_journal_article(references) -> None:
    proceedings = references[0].csl
    assert proceedings["type"] == "paper-conference"
    assert proceedings["container-title"] == "Advances in Neural Information Processing Systems"


def test_case_3_container_title_present_for_every_analytic(references) -> None:
    for reference in references:
        if reference.csl and reference.csl.get("type") in {"article-journal", "paper-conference"}:
            assert reference.csl.get("container-title"), f"{reference.ref_id} lost its container"


def test_case_4_page_ranges_come_from_attributes(references) -> None:
    """`<biblScope unit="page" from="1" to="28"/>` carries no element text at all."""
    assert references[0].csl["page"] == "5998-6008"
    assert references[1].csl["page"] == "1-28"


def test_dates_volumes_and_dois(references) -> None:
    assert references[0].csl["issued"] == {"date-parts": [[2017]]}
    assert references[1].csl["issued"] == {"date-parts": [[2022, 9, 15]]}
    assert references[1].csl["volume"] == "55"
    assert references[1].csl["issue"] == "6"
    assert references[1].csl["DOI"] == "10.1145/3530811"


def test_raw_string_is_retained_verbatim(references) -> None:
    """HR-3. Whatever else happens to an entry, the paper's own characters survive."""
    assert references[0].raw_string.startswith('A. Vaswani, N. Shazeer, "Attention is all you need,"')
    assert references[3].raw_string == "Smith, J. mumble mumble 20??, pp. ??-??"


# ---------------------------------------------------------------- confidence and tiers


def test_doi_floors_the_confidence(references) -> None:
    """A DOI means GROBID's consolidateCitations matched an external record."""
    assert references[1].parse_confidence >= 0.90


def test_unparseable_entry_is_quarantined_not_dropped(references) -> None:
    assert references[3].tier == ConfidenceTier.QUARANTINED
    assert references[3].csl is None
    assert references[3].raw_string  # kept verbatim


def test_nothing_is_resolved_before_the_arbiter_runs(references) -> None:
    """'Resolved' means an external record agreed. No external call has happened."""
    assert all(r.tier != ConfidenceTier.RESOLVED for r in references)
    assert all(r.source_id is None for r in references)
    assert all(r.agreement_score is None for r in references)


def test_every_biblstruct_becomes_a_reference(references) -> None:
    assert len(references) == 4
    assert [r.ref_id for r in references] == ["b0", "b1", "b2", "b3"]


def test_confidence_does_not_penalise_a_book_for_having_no_container() -> None:
    book = {
        "type": "book",
        "title": "T",
        "author": [{"family": "A"}],
        "issued": {"date-parts": [[2009]]},
        "page": "1",
    }
    assert parse_confidence_for(book) == pytest.approx(1.0)


# ---------------------------------------------------------------- failure handling


def test_malformed_tei_raises_rather_than_recovering() -> None:
    """Half a document silently recovered is worse than a reported failure."""
    with pytest.raises(GrobidParseError, match="malformed TEI"):
        parse_tei("<TEI><text><body><p>unclosed")


def test_empty_tei_raises() -> None:
    with pytest.raises(GrobidParseError, match="empty TEI"):
        parse_tei("   ")


def test_non_tei_root_raises() -> None:
    with pytest.raises(GrobidParseError, match="expected a <TEI> root"):
        parse_tei("<html><body/></html>")


def test_tei_without_a_body_raises() -> None:
    """A PDF with no text layer is reported, not turned into an empty document."""
    minimal = '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader/><text/></TEI>'
    with pytest.raises(GrobidParseError, match="no <body>"):
        tei_to_ir(minimal, doc_id="doc_empty")
