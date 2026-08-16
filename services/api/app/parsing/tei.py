"""TEI → Document IR.

The single most valuable thing GROBID gives us is the link between an in-text marker and
a bibliography entry: `<ref type="bibr" target="#b12">` points at
`<biblStruct xml:id="b12">`. That linkage is the precondition for HR-5 — knowing which
citation an anchor *is* — and it is not reconstructible from text without guessing. We
read it; we never rebuild it.

Two mapping decisions worth knowing about:

**A span is a sentence.** GROBID is asked for `segmentSentences=1`, so `<p>` contains
`<s>` elements and each becomes a `Span`. Reattachment after a rewrite scores an
anchor's context fingerprint against sentences (ADR-013), and claims carry a `span_id`
(CP-5); both are natural when a span is a sentence and awkward when it is a paragraph.
A `<p>` with no `<s>` children falls back to one span for the whole paragraph.

**Marker text is removed from the span text and kept on the anchor.** The characters
`[12]` do not live in the string; the anchor records where they were and what they said.
That is ADR-004's "anchors are nodes, not characters", and it is what lets citeproc
re-render the citation in a different style at the same position.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

from app.core.contracts import (
    Block,
    CitationAnchor,
    Document,
    DocumentMeta,
    QuarantineEntry,
    Section,
    Span,
)
from app.core.errors import GrobidParseError
from app.ir import ids
from app.parsing.models import Coordinate, OrphanMarker, ParsedDocument

__all__ = ["tei_to_ir", "TEI_NS", "parse_tei"]

TEI_NS = "http://www.tei-c.org/ns/1.0"
_NS = {"tei": TEI_NS}

# A figure whose type is "table" is a table; GROBID overloads the element.
_TABLE_TYPE = "table"

_WS = re.compile(r"[ \t\r\f\v]+")


def parse_tei(tei_xml: str) -> etree._Element:
    """Parse TEI, raising `GrobidParseError` on anything unusable."""
    if not tei_xml or not tei_xml.strip():
        raise GrobidParseError("empty TEI document")
    try:
        # `recover=False`: a truncated TEI means a truncated paper, and silently
        # recovering half a document is worse than reporting that we got half.
        parser = etree.XMLParser(recover=False, resolve_entities=False, no_network=True)
        root = etree.fromstring(tei_xml.encode("utf-8"), parser=parser)
    except etree.XMLSyntaxError as exc:
        raise GrobidParseError(f"GROBID returned malformed TEI: {exc}") from exc
    if not root.tag.endswith("TEI"):
        raise GrobidParseError(f"expected a <TEI> root element, got <{etree.QName(root).localname}>")
    return root


def _text(element: etree._Element | None) -> str:
    if element is None:
        return ""
    return _WS.sub(" ", "".join(element.itertext())).strip()


def _coords(element: etree._Element) -> list[Coordinate]:
    raw = element.get("coords")
    return Coordinate.parse(raw) if raw else []


def _first_page(boxes: list[Coordinate]) -> int | None:
    return boxes[0].page if boxes else None


@dataclass
class _SpanAssembly:
    """A span under construction: text so far, plus the anchors placed inside it."""

    parts: list[str]
    anchors: list[tuple[int, str, str | None]]  # (offset, marker_text, target)

    @property
    def text(self) -> str:
        return "".join(self.parts)


def _collect_inline(
    element: etree._Element,
    assembly: _SpanAssembly,
    *,
    include_tail: bool = False,
) -> None:
    """Walk an element's mixed content, pulling bibliographic refs out as anchors.

    Everything that is not a `type="bibr"` ref keeps its text inline — a reference to a
    figure or an equation is prose, not a citation.
    """
    if element.text:
        assembly.parts.append(element.text)

    for child in element:
        local = etree.QName(child).localname
        if local == "ref" and child.get("type") == "bibr":
            marker = _text(child)
            assembly.anchors.append((len(assembly.text), marker, child.get("target")))
            # The marker's characters are deliberately not appended: the anchor holds
            # them now. The tail (", " between grouped markers, the closing full stop)
            # still belongs to the sentence.
            if child.tail:
                assembly.parts.append(child.tail)
        else:
            _collect_inline(child, assembly, include_tail=True)

    if include_tail and element.tail:
        assembly.parts.append(element.tail)


def _build_span(
    element: etree._Element,
    *,
    doc_id: str,
    section_order: int,
    block_order: int,
    span_index: int,
    known_ref_ids: set[str],
    section_id: str,
    anchor_to_ref: dict[str, str],
    orphans: list[OrphanMarker],
    coordinates: dict[str, list[Coordinate]],
) -> Span:
    assembly = _SpanAssembly(parts=[], anchors=[])
    _collect_inline(element, assembly)

    text = _WS.sub(" ", assembly.text).strip()
    span_id = ids.stable_id(ids.SPAN, doc_id, section_order, block_order, span_index)
    boxes = _coords(element)
    if boxes:
        coordinates[span_id] = boxes

    span = Span(id=span_id, text=text)
    for marker_index, (offset, marker, target) in enumerate(assembly.anchors):
        anchor_id = ids.stable_id(ids.ANCHOR, doc_id, span_id, marker_index)
        ref_id = target.lstrip("#") if target else ""

        if not ref_id or ref_id not in known_ref_ids:
            # HR-3: detected, located, surfaced. The anchor is still created so the
            # marker survives export and the user can see exactly where it sits.
            orphans.append(
                OrphanMarker(
                    anchor_id=anchor_id,
                    marker_text=marker,
                    target=target,
                    section_id=section_id,
                    span_id=span_id,
                    page=_first_page(boxes),
                )
            )
        else:
            anchor_to_ref[anchor_id] = ref_id

        span.citation_anchors.append(
            CitationAnchor(
                anchor_id=anchor_id,
                # Empty until the arbiter resolves this reference to a real record.
                # See app/parsing/models.py for why this is not GROBID's local id.
                source_ids=[],
                offset_in_span=min(max(0, offset), len(text)),
                original_marker_text=marker or None,
                provenance_kind="parsed",
            )
        )
    return span


def _block_type(element: etree._Element) -> str:
    local = etree.QName(element).localname
    if local == "formula":
        return "equation"
    if local == "figure":
        return _TABLE_TYPE if element.get("type") == _TABLE_TYPE else "figure"
    if local == "list":
        return "list"
    return "paragraph"


def _caption(element: etree._Element) -> str:
    """Caption for a figure/table/equation placeholder. ADR-008 requires one."""
    head = _text(element.find("tei:head", _NS))
    desc = _text(element.find("tei:figDesc", _NS))
    caption = " ".join(part for part in (head, desc) if part).strip()
    if caption:
        return caption
    # An equation has no caption; carry its text so the placeholder still says
    # something specific rather than just "equation omitted".
    own = _text(element)
    return own[:300] if own else "(no caption extracted)"


def _section_page(section: Section, coordinates: dict[str, list[Coordinate]]) -> int | None:
    """The page a section starts on, read from the first of its parts that has coordinates."""
    for key in (section.id, *(b.id for b in section.blocks)):
        page = _first_page(coordinates.get(key, []))
        if page is not None:
            return page
    return None


def _place_floats(
    floats: list[etree._Element],
    *,
    sections: list[Section],
    doc_id: str,
    coordinates: dict[str, list[Coordinate]],
) -> None:
    """Attach each floating figure/table/equation to the section printed on its page.

    Falls back to the last section when a float has no coordinates or lands before the
    first section that does — appending is not ideal, but it keeps the block in the
    document, and a float silently dropped is worse than a float in the wrong place.
    """
    if not floats or not sections:
        return

    pages = [(index, _section_page(s, coordinates)) for index, s in enumerate(sections)]
    known = [(index, page) for index, page in pages if page is not None]

    for element in floats:
        boxes = _coords(element)
        page = _first_page(boxes)

        target = len(sections) - 1
        if page is not None and known:
            # The last section that had started by the time this page was typeset.
            started = [index for index, section_page in known if section_page <= page]
            target = started[-1] if started else known[0][0]

        section = sections[target]
        block_id = ids.stable_id(ids.BLOCK, doc_id, section.order, len(section.blocks))
        if boxes:
            coordinates[block_id] = boxes
        section.blocks.append(
            Block(
                id=block_id,
                type=_block_type(element),  # type: ignore[arg-type]
                order=len(section.blocks),
                spans=[],
                placeholder_caption=_caption(element),
            )
        )


def _section_level(head: etree._Element | None) -> int:
    """GROBID numbers headings in `@n` — "3.1" is a level-2 heading."""
    if head is None:
        return 1
    numbering = (head.get("n") or "").strip().rstrip(".")
    if not numbering:
        return 1
    return max(1, numbering.count(".") + 1)


def tei_to_ir(tei_xml: str, *, doc_id: str) -> ParsedDocument:
    """Map a GROBID TEI document to the IR.

    References themselves are mapped by `app.parsing.csl`; this returns them as raw
    `biblStruct` elements' ids so the caller can join the two. The anchor→reference map
    is the output that matters most.
    """
    root = parse_tei(tei_xml)

    bibl_structs = root.findall(".//tei:back//tei:biblStruct", namespaces=_NS)
    known_ref_ids = {
        b.get("{http://www.w3.org/XML/1998/namespace}id", "")
        for b in bibl_structs
    } - {""}

    anchor_to_ref: dict[str, str] = {}
    orphans: list[OrphanMarker] = []
    coordinates: dict[str, list[Coordinate]] = {}
    sections: list[Section] = []
    quarantine: list[QuarantineEntry] = []

    title = _text(
        root.find(".//tei:teiHeader//tei:titleStmt/tei:title", namespaces=_NS)
    ) or None

    section_order = 0

    def _add_section(heading: str, level: int, source_elements: list[etree._Element],
                     head_element: etree._Element | None = None) -> None:
        nonlocal section_order
        section_id = ids.stable_id(ids.SECTION, doc_id, section_order)
        if head_element is not None:
            boxes = _coords(head_element)
            if boxes:
                coordinates[section_id] = boxes

        blocks: list[Block] = []
        for block_order, element in enumerate(source_elements):
            block_type = _block_type(element)
            block_id = ids.stable_id(ids.BLOCK, doc_id, section_order, block_order)
            boxes = _coords(element)
            if boxes:
                coordinates[block_id] = boxes

            if block_type in {"figure", "table", "equation"}:
                blocks.append(
                    Block(
                        id=block_id,
                        type=block_type,  # type: ignore[arg-type]
                        order=block_order,
                        spans=[],
                        placeholder_caption=_caption(element),
                    )
                )
                continue

            sentence_elements = element.findall("tei:s", namespaces=_NS)
            targets = sentence_elements or [element]
            spans = [
                _build_span(
                    target,
                    doc_id=doc_id,
                    section_order=section_order,
                    block_order=block_order,
                    span_index=span_index,
                    known_ref_ids=known_ref_ids,
                    section_id=section_id,
                    anchor_to_ref=anchor_to_ref,
                    orphans=orphans,
                    coordinates=coordinates,
                )
                for span_index, target in enumerate(targets)
            ]
            spans = [s for s in spans if s.text or s.citation_anchors]
            if not spans:
                continue
            blocks.append(
                Block(
                    id=block_id,
                    type="paragraph" if block_type == "paragraph" else block_type,  # type: ignore[arg-type]
                    order=len(blocks),
                    spans=spans,
                )
            )

        if not blocks and not heading:
            return
        sections.append(
            Section(id=section_id, level=level, title=heading, order=section_order, blocks=blocks)
        )
        section_order += 1

    # The abstract lives in the header, not the body, but it is a section of the paper
    # and reviewers cite it. Dropping it would silently shorten every document.
    abstract = root.find(".//tei:profileDesc/tei:abstract", namespaces=_NS)
    if abstract is not None:
        abstract_paragraphs = abstract.findall(".//tei:p", namespaces=_NS)
        if abstract_paragraphs:
            _add_section("Abstract", 1, abstract_paragraphs)

    body = root.find(".//tei:text/tei:body", namespaces=_NS)
    if body is None:
        raise GrobidParseError(
            "TEI has no <body>. GROBID parsed the file but found no document text — "
            "usually a scanned PDF with no text layer."
        )

    for div in body.findall("tei:div", namespaces=_NS):
        head = div.find("tei:head", namespaces=_NS)
        children = [
            child
            for child in div
            if etree.QName(child).localname in {"p", "formula", "figure", "list"}
        ]
        _add_section(_text(head), _section_level(head), children, head)

    # Floating figures, tables and equations are siblings of the divs, not children —
    # GROBID hoists them out of the flow because that is where the PDF put them. Parking
    # them all in one trailing "Figures and Tables" section, as this used to, moves every
    # figure in the paper to the back and reads nothing like the original. Their
    # coordinates carry the page they were printed on, so each one goes to the section
    # that was being typeset on that page instead.
    floats = [
        child
        for child in body
        if etree.QName(child).localname in {"figure", "formula"}
    ]
    _place_floats(
        floats,
        sections=sections,
        doc_id=doc_id,
        coordinates=coordinates,
    )

    for orphan in orphans:
        quarantine.append(
            QuarantineEntry(
                raw=orphan.marker_text or "(empty marker)",
                reason="orphan_marker",
                page=orphan.page,
            )
        )

    document = Document(
        doc_id=doc_id,
        version=1,
        metadata=DocumentMeta(title=title),
        sections=sections,
        quarantine=quarantine,
    )

    for bibl in bibl_structs:
        ref_id = bibl.get("{http://www.w3.org/XML/1998/namespace}id", "")
        boxes = _coords(bibl)
        if ref_id and boxes:
            coordinates[ref_id] = boxes

    return ParsedDocument(
        document=document,
        references=[],  # filled by app.parsing.csl
        anchor_to_ref=anchor_to_ref,
        orphan_markers=orphans,
        coordinates=coordinates,
        raw_tei=tei_xml,
    )
