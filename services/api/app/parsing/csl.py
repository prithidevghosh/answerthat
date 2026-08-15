"""`biblStruct` → provisional CSL-JSON, with a parse confidence.

CSL-JSON is the only citation model in this system (HR-4), so this mapping is where a
reference becomes something the rest of the pipeline can reason about.

Four places this goes wrong, all of them tested explicitly:

1. **Name particles.** `van der Berg` is a family name of `Berg` with a non-dropping
   particle, not a given name of `van der`. Get it wrong and APA renders `D. van der
   Berg` as `Berg, D. V. D.`
2. **`analytic` vs `monogr`.** With an `<analytic>`, the monogr title is the *container*
   (the journal). Without one, the monogr title is the *work itself* (a book). Confusing
   them turns every book into an article with no title.
3. **container-title.** Follows directly from (2), and is the field most likely to be
   silently empty.
4. **Page ranges.** `<biblScope unit="page" from="1" to="10"/>` carries its value in
   attributes, not text — a naive text read yields an empty string.

`parse_confidence` is completeness-weighted, floored high when GROBID's citation
consolidation produced a DOI: that means GROBID matched the reference against an
external record, which is a much stronger signal than any count of populated fields.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

from app.parsing.tei import TEI_NS

__all__ = ["biblstruct_to_csl", "parse_confidence_for", "raw_reference_string", "CSL_FIELD_WEIGHTS"]

_NS = {"tei": TEI_NS}
_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
_WS = re.compile(r"\s+")

# Completeness weights. Title and authors dominate because they are what the arbiter
# matches on; a reference missing both is not a reference we can do anything with.
CSL_FIELD_WEIGHTS: dict[str, float] = {
    "title": 0.35,
    "author": 0.25,
    "issued": 0.15,
    "container-title": 0.15,
    "page": 0.10,
}

# A DOI means GROBID's consolidateCitations matched this entry against Crossref.
_DOI_CONFIDENCE_FLOOR = 0.90


def _text(element: etree._Element | None) -> str:
    if element is None:
        return ""
    return _WS.sub(" ", "".join(element.itertext())).strip()


def raw_reference_string(bibl: etree._Element) -> str:
    """The verbatim reference text, from `includeRawCitations=1`.

    Retained unchanged for quarantine display (HR-3) and as the comparison target for
    style detection (ADR-011). If GROBID was not asked for raw citations this is empty,
    and both of those features silently lose their ground truth — hence the check in
    `GrobidOptions`.
    """
    for note in bibl.findall("tei:note", namespaces=_NS):
        if note.get("type") == "raw_reference":
            return _text(note)
    return ""


def _person_to_csl(pers_name: etree._Element) -> dict[str, str]:
    """A `persName` → a CSL name object, particles intact."""
    given = " ".join(
        _text(f) for f in pers_name.findall("tei:forename", namespaces=_NS) if _text(f)
    ).strip()
    family = _text(pers_name.find("tei:surname", namespaces=_NS))
    # TEI marks particles with <nameLink>. CSL calls them non-dropping-particles, and
    # citeproc needs them separate so sorting and initialising behave correctly.
    particles = " ".join(
        _text(n) for n in pers_name.findall("tei:nameLink", namespaces=_NS) if _text(n)
    ).strip()

    if not family and given:
        # A single unlabelled token: treat it as the family name rather than inventing
        # a split. Guessing here is how "Tay" becomes a given name with no surname.
        family, given = given, ""

    name: dict[str, str] = {}
    if family:
        name["family"] = family
    if given:
        name["given"] = given
    if particles:
        name["non-dropping-particle"] = particles
    return name


def _authors(parent: etree._Element | None) -> list[dict[str, str]]:
    if parent is None:
        return []
    people: list[dict[str, str]] = []
    for author in parent.findall("tei:author", namespaces=_NS):
        pers_name = author.find("tei:persName", namespaces=_NS)
        if pers_name is not None:
            name = _person_to_csl(pers_name)
            if name:
                people.append(name)
            continue
        # An institutional author has no persName. CSL represents it as a literal.
        org = _text(author.find("tei:orgName", namespaces=_NS)) or _text(author)
        if org:
            people.append({"literal": org})
    return people


def _issued(monogr: etree._Element | None) -> dict[str, Any] | None:
    """`<date when="2017-06-12"/>` → CSL date-parts, as precise as the source is."""
    if monogr is None:
        return None
    for date in monogr.findall(".//tei:date", namespaces=_NS):
        when = (date.get("when") or "").strip() or _text(date)
        match = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", when)
        if match:
            parts = [int(g) for g in match.groups() if g]
            return {"date-parts": [parts]}
    return None


def _pages(monogr: etree._Element | None) -> str | None:
    """Page range from `from`/`to` attributes, falling back to element text."""
    if monogr is None:
        return None
    for scope in monogr.findall(".//tei:biblScope", namespaces=_NS):
        if scope.get("unit") != "page":
            continue
        start, end = scope.get("from"), scope.get("to")
        if start and end:
            return f"{start}-{end}"
        if start:
            return start
        text = _text(scope)
        if text:
            return text
    return None


def _scope(monogr: etree._Element | None, unit: str) -> str | None:
    if monogr is None:
        return None
    for scope in monogr.findall(".//tei:biblScope", namespaces=_NS):
        if scope.get("unit") == unit:
            value = _text(scope) or scope.get("from") or ""
            if value:
                return value
    return None


def _idno(element: etree._Element | None, kind: str) -> str | None:
    if element is None:
        return None
    for idno in element.findall(".//tei:idno", namespaces=_NS):
        if (idno.get("type") or "").upper() == kind.upper():
            value = _text(idno)
            if value:
                return value
    return None


def _monogr_title(monogr: etree._Element | None, level: str | None = None) -> tuple[str, str | None]:
    """The monogr title and its `@level`, which says what kind of container it is."""
    if monogr is None:
        return "", None
    for title in monogr.findall("tei:title", namespaces=_NS):
        title_level = title.get("level")
        if level is None or title_level == level:
            text = _text(title)
            if text:
                return text, title_level
    return "", None


def _csl_type(has_analytic: bool, container_level: str | None, monogr: etree._Element | None) -> str:
    """Decide the CSL type. This is decision (2), and everything else follows from it."""
    is_meeting = monogr is not None and monogr.find(".//tei:meeting", namespaces=_NS) is not None

    if has_analytic:
        if is_meeting:
            return "paper-conference"
        if container_level == "j":
            return "article-journal"
        if container_level == "m":
            # An analytic inside a monograph is a chapter, not an article.
            return "chapter"
        return "article-journal"

    # No analytic: the monogr describes the work itself.
    if is_meeting:
        return "paper-conference"
    if monogr is not None and _text(monogr.find(".//tei:publisher", namespaces=_NS)):
        return "book"
    return "document"


def biblstruct_to_csl(bibl: etree._Element) -> tuple[dict[str, Any], float, str]:
    """Map one `biblStruct` to `(csl_json, parse_confidence, raw_string)`.

    The CSL dict is *provisional*: the arbiter may replace it wholesale with an external
    record (ADR-001). The raw string is not provisional — it is what the PDF actually
    said, and it is retained whatever happens.
    """
    analytic = bibl.find("tei:analytic", namespaces=_NS)
    monogr = bibl.find("tei:monogr", namespaces=_NS)
    has_analytic = analytic is not None

    csl: dict[str, Any] = {}

    # --- title and container-title: decisions (2) and (3) ---
    monogr_title, monogr_level = _monogr_title(monogr)
    if has_analytic:
        analytic_title = _text(analytic.find("tei:title", namespaces=_NS))
        if analytic_title:
            csl["title"] = analytic_title
        if monogr_title:
            csl["container-title"] = monogr_title
    elif monogr_title:
        # No analytic: the monogr title is the work, and there is no container.
        csl["title"] = monogr_title

    csl["type"] = _csl_type(has_analytic, monogr_level, monogr)

    # --- authors: decision (1) ---
    authors = _authors(analytic) or _authors(monogr)
    if authors:
        csl["author"] = authors

    editors: list[dict[str, str]] = []
    if monogr is not None:
        for editor in monogr.findall("tei:editor", namespaces=_NS):
            pers_name = editor.find("tei:persName", namespaces=_NS)
            name = _person_to_csl(pers_name) if pers_name is not None else {}
            if name:
                editors.append(name)
    if editors:
        csl["editor"] = editors

    # --- dates, scopes: decision (4) ---
    issued = _issued(monogr)
    if issued:
        csl["issued"] = issued
    pages = _pages(monogr)
    if pages:
        csl["page"] = pages
    volume = _scope(monogr, "volume")
    if volume:
        csl["volume"] = volume
    issue = _scope(monogr, "issue")
    if issue:
        csl["issue"] = issue

    publisher = _text(monogr.find(".//tei:publisher", namespaces=_NS)) if monogr is not None else ""
    if publisher:
        csl["publisher"] = publisher

    doi = _idno(bibl, "DOI") or _idno(analytic, "DOI") or _idno(monogr, "DOI")
    if doi:
        csl["DOI"] = doi.lower()
    for kind, field in (("PMID", "PMID"), ("PMCID", "PMCID"), ("arXiv", "number")):
        value = _idno(bibl, kind)
        if value and field not in csl:
            csl[field] = value

    raw = raw_reference_string(bibl)
    if raw:
        # Keep the verbatim string on the record itself so it travels with the entry.
        csl["note"] = raw

    ref_id = bibl.get(_XML_ID) or ""
    if ref_id:
        csl["id"] = ref_id

    return csl, parse_confidence_for(csl), raw


def parse_confidence_for(csl: dict[str, Any]) -> float:
    """Weighted field completeness, floored high when a DOI is present.

    Deliberately not a model's opinion of its own output. This number decides whether
    the constrained repair tier runs (ADR-003), so it has to be something we can explain
    and reproduce.
    """
    score = 0.0
    for field, weight in CSL_FIELD_WEIGHTS.items():
        value = csl.get(field)
        if field == "container-title" and csl.get("type") in {"book", "document"}:
            # A standalone work has no container; not having one is not incompleteness,
            # so award the weight rather than penalising a correctly parsed book.
            score += weight
            continue
        if value:
            score += weight
    if csl.get("DOI"):
        return max(score, _DOI_CONFIDENCE_FLOOR)
    return round(score, 4)
