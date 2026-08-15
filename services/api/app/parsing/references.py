"""Extract every `biblStruct` from a TEI document as a `ParsedReference`.

"Every" is load-bearing. The count that comes out of here is `total_detected`, and the
tier invariant is checked against it from this point onward — so an entry skipped here
would never be missed downstream, which is precisely why nothing is skipped here.
"""

from __future__ import annotations

from lxml import etree

from app.core.contracts import ParsedReference
from app.parsing.csl import biblstruct_to_csl
from app.parsing.tei import TEI_NS
from app.parsing.tiers import initial_tier

__all__ = ["references_from_tei", "extract_references"]

_NS = {"tei": TEI_NS}
_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


def references_from_tei(root: etree._Element, *, threshold: float) -> list[ParsedReference]:
    references: list[ParsedReference] = []
    for index, bibl in enumerate(root.findall(".//tei:back//tei:biblStruct", namespaces=_NS)):
        ref_id = bibl.get(_XML_ID) or f"b{index}"
        csl, confidence, raw = biblstruct_to_csl(bibl)

        # A reference with no raw string still exists. Falling back to a rendering of
        # what we parsed keeps the entry displayable; it is marked by the absence of a
        # `note` field rather than by an invented string.
        references.append(
            ParsedReference(
                ref_id=ref_id,
                raw_string=raw,
                csl=csl if csl.get("title") or csl.get("DOI") else None,
                tier=initial_tier(csl, confidence, raw, threshold=threshold),
                parse_confidence=confidence,
                agreement_score=None,
                source_id=None,
            )
        )
    return references


def extract_references(tei_xml: str, *, threshold: float) -> list[ParsedReference]:
    from app.parsing.tei import parse_tei

    return references_from_tei(parse_tei(tei_xml), threshold=threshold)
