"""Provider payloads → CSL-JSON.

HR-4: CSL-JSON is the only citation model in this system. Adapters produce CSL *data*;
nothing here ever produces a formatted citation *string* — that is citeproc's job, in
`app/export/` and in the frontend, both reading the same `.csl` files.

Provider-specific identifiers live under CSL's `custom` extension namespace rather than
as invented top-level fields, because CSL-JSON has a schema and we do not get to extend
it. `identity.extract_identifiers()` reads them back out.

The fiddly part is names. S2 and OpenAlex both hand back a single display string
(`"Ashish Vaswani"`), and citeproc needs `family`/`given` to render an author-date cite as
"Vaswani, 2017" rather than "Ashish Vaswani, 2017". The alternative — CSL's `literal`
form — is lossless but renders wrongly in exactly the styles our shortlist is full of. So
we split, conservatively, with a particle list, and keep the provider's original string in
`custom.raw_author_names` so a bad split is auditable rather than silent.
"""

from __future__ import annotations

from typing import Any

from app.providers.identity import normalize_doi, normalize_openalex_id

__all__ = ["split_person_name", "s2_paper_to_csl", "openalex_work_to_csl", "crossref_item_to_csl"]

# Lowercased name particles that belong with the family name, not the given name.
# Multi-word particles are matched greedily ("van der Berg" -> family "van der Berg").
_PARTICLES = {
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "das", "dos", "du",
    "la", "le", "les", "lo", "ter", "ten", "bin", "ibn", "al", "el", "abu", "af", "av",
}

_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "ph.d."}


def split_person_name(display_name: str) -> dict[str, str]:
    """Best-effort `{family, given}` from a display string.

    Handles the two forms providers actually emit: `"Given Family"` and `"Family, Given"`.
    A single-token name becomes a `literal`, because guessing which half is missing would
    be worse than declining to guess.
    """
    name = " ".join((display_name or "").split())
    if not name:
        return {"literal": ""}

    if "," in name:
        family, _, given = name.partition(",")
        family, given = family.strip(), given.strip()
        if family and given:
            return {"family": family, "given": given}
        return {"literal": name}

    tokens = name.split()
    if len(tokens) == 1:
        # A corporate author, a mononym, or a mangled record. `literal` renders it
        # verbatim, which is the honest outcome for all three.
        return {"literal": name}

    suffix = ""
    if tokens[-1].lower().strip(",") in _SUFFIXES and len(tokens) > 2:
        suffix = tokens[-1].strip(",")
        tokens = tokens[:-1]

    # Walk backwards from the last token over any particles.
    split_at = len(tokens) - 1
    while split_at > 1 and tokens[split_at - 1].lower() in _PARTICLES:
        split_at -= 1

    given = " ".join(tokens[:split_at])
    family = " ".join(tokens[split_at:])
    person = {"family": family, "given": given}
    if suffix:
        person["suffix"] = suffix
    return person


def _issued(year: int | None) -> dict[str, Any] | None:
    return {"date-parts": [[year]]} if isinstance(year, int) and year > 0 else None


def _prune(csl: dict[str, Any]) -> dict[str, Any]:
    """Drop empty values so the append-only store's enrichment rule can fill them later."""
    return {k: v for k, v in csl.items() if v not in (None, "", [], {})}


# --------------------------------------------------------------------------- Semantic Scholar


def _s2_type(paper: dict[str, Any]) -> str:
    types = {t.lower() for t in (paper.get("publicationTypes") or []) if t}
    if "conference" in types:
        return "paper-conference"
    if "book" in types or "booksection" in types:
        return "chapter" if "booksection" in types else "book"
    if "review" in types or "journalarticle" in types:
        return "article-journal"
    venue_type = ((paper.get("publicationVenue") or {}).get("type") or "").lower()
    if venue_type == "conference":
        return "paper-conference"
    if not paper.get("venue") and not (paper.get("journal") or {}).get("name"):
        # No venue at all is the shape of a preprint or a dataset record.
        return "article"
    return "article-journal"


def _provider_facts(provider: str, **facts: Any) -> dict[str, Any] | None:
    """Per-provider values, filed under the provider that said them. ADR-028.

    Two kinds of thing end up here, and neither is a property of the *work*:

    * **Measurements that change.** A citation count read today and read next month are
      both correct and different. Storing one as a value that may never change was a
      category error, and it is what made Crossref's 164 collide with OpenAlex's 349.
    * **A provider's own rendering.** `raw_author_names` is how that provider spelled the
      authors, kept so a bad name split stays auditable. Two providers spelling them
      differently is expected, not a contradiction.

    Namespacing means the store's key-by-key merge can never see them as rivals: each
    provider writes under its own key and the others are simply absent. Identifiers stay
    flat above — those *are* stable facts about the work, and `extract_identifiers` reads
    them from there.
    """
    pruned = {k: v for k, v in facts.items() if v not in (None, "", [], {})}
    return {provider: pruned} if pruned else None


def s2_paper_to_csl(paper: dict[str, Any]) -> dict[str, Any]:
    external = paper.get("externalIds") or {}
    journal = paper.get("journal") or {}
    venue_obj = paper.get("publicationVenue") or {}
    authors = [a for a in (paper.get("authors") or []) if a]

    csl: dict[str, Any] = {
        "type": _s2_type(paper),
        "title": paper.get("title"),
        "author": [split_person_name(a.get("name", "")) for a in authors],
        "issued": _issued(paper.get("year")),
        "container-title": journal.get("name") or venue_obj.get("name") or paper.get("venue"),
        "volume": journal.get("volume"),
        "page": journal.get("pages"),
        "DOI": normalize_doi(external.get("DOI")),
        "URL": paper.get("url"),
        "custom": _prune(
            {
                "s2_paper_id": paper.get("paperId"),
                "s2_corpus_id": str(paper["corpusId"]) if paper.get("corpusId") else None,
                "arxiv_id": external.get("ArXiv"),
                "pmid": str(external["PubMed"]) if external.get("PubMed") else None,
                "providers": _provider_facts(
                    "semantic_scholar",
                    citation_count=paper.get("citationCount"),
                    raw_author_names=[a.get("name") for a in authors if a.get("name")],
                ),
            }
        ),
    }
    return _prune(csl)


# --------------------------------------------------------------------------- OpenAlex


#: Crossref publishes its *own* type vocabulary, not CSL's — `journal-article` where CSL
#: says `article-journal`, `proceedings-article` where CSL says `paper-conference`. It was
#: being passed through unmapped, which put non-CSL values in the store and rendered
#: wrongly through citeproc (HR-4). Sharing `_OPENALEX_TYPE_TO_CSL` would be an accident
#: waiting to happen — the two vocabularies overlap but are not the same — so it is spelled
#: out separately.
_CROSSREF_TYPE_TO_CSL = {
    "journal-article": "article-journal",
    "proceedings-article": "paper-conference",
    "book-chapter": "chapter",
    "book-part": "chapter",
    "book-section": "chapter",
    "book": "book",
    "monograph": "book",
    "edited-book": "book",
    "reference-book": "book",
    "posted-content": "article",
    "dissertation": "thesis",
    "report": "report",
    "report-component": "report",
    "dataset": "dataset",
    "journal-issue": "article-journal",
    "proceedings": "paper-conference",
    "standard": "report",
    "peer-review": "review",
    "other": "article-journal",
}

_OPENALEX_TYPE_TO_CSL = {
    "article": "article-journal",
    "journal-article": "article-journal",
    "book": "book",
    "book-chapter": "chapter",
    "proceedings-article": "paper-conference",
    "dissertation": "thesis",
    "report": "report",
    "dataset": "dataset",
    "preprint": "article",
    "posted-content": "article",
    "review": "article-journal",
}


def openalex_work_to_csl(work: dict[str, Any]) -> dict[str, Any]:
    ids = work.get("ids") or {}
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    biblio = work.get("biblio") or {}
    authorships = [a for a in (work.get("authorships") or []) if a]

    page = None
    first, last = biblio.get("first_page"), biblio.get("last_page")
    if first and last and first != last:
        page = f"{first}-{last}"
    elif first:
        page = str(first)

    authors = []
    for authorship in authorships:
        raw = authorship.get("raw_author_name") or (authorship.get("author") or {}).get(
            "display_name", ""
        )
        authors.append(split_person_name(raw))

    csl: dict[str, Any] = {
        "type": _OPENALEX_TYPE_TO_CSL.get((work.get("type") or "").lower(), "article-journal"),
        "title": work.get("title") or work.get("display_name"),
        "author": authors,
        "issued": _issued(work.get("publication_year")),
        "container-title": source.get("display_name"),
        "volume": biblio.get("volume"),
        "issue": biblio.get("issue"),
        "page": page,
        "DOI": normalize_doi(work.get("doi") or ids.get("doi")),
        "URL": location.get("landing_page_url") or ids.get("openalex"),
        "publisher": source.get("host_organization_name"),
        "custom": _prune(
            {
                # Identifiers stay flat: they are stable facts about the work, each
                # provider contributes different ones, and `identity.extract_identifiers`
                # reads them from here.
                "openalex_id": normalize_openalex_id(work.get("id") or ids.get("openalex")),
                "pmid": _tail(ids.get("pmid")),
                "arxiv_id": _arxiv_from_openalex(work),
                "providers": _provider_facts(
                    "openalex",
                    citation_count=work.get("cited_by_count"),
                    is_open_access=(work.get("open_access") or {}).get("is_oa"),
                    raw_author_names=[
                        a.get("raw_author_name") or (a.get("author") or {}).get("display_name")
                        for a in authorships
                    ],
                ),
            }
        ),
    }
    return _prune(csl)


def _tail(url: str | None) -> str | None:
    return url.rstrip("/").rsplit("/", 1)[-1] if url else None


def _arxiv_from_openalex(work: dict[str, Any]) -> str | None:
    for location in work.get("locations") or []:
        source = (location or {}).get("source") or {}
        if "arxiv" in (source.get("display_name") or "").lower():
            landing = location.get("landing_page_url") or ""
            if "/abs/" in landing:
                return landing.rsplit("/abs/", 1)[-1]
    return None


# --------------------------------------------------------------------------- Crossref


def crossref_item_to_csl(item: dict[str, Any]) -> dict[str, Any]:
    """Crossref already speaks something very close to CSL-JSON — it is where CSL came from.

    We still map explicitly rather than passing the payload through: Crossref returns
    `title` and `container-title` as *arrays*, and a downstream citeproc handed an array
    where it expects a string renders it wrongly rather than failing.
    """
    issued_parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    year = issued_parts[0] if issued_parts and isinstance(issued_parts[0], int) else None

    authors = []
    for person in item.get("author") or []:
        if person.get("family"):
            entry = {"family": person["family"]}
            if person.get("given"):
                entry["given"] = person["given"]
            if person.get("suffix"):
                entry["suffix"] = person["suffix"]
            authors.append(entry)
        elif person.get("name"):
            # Crossref's form for corporate authors.
            authors.append({"literal": person["name"]})

    csl: dict[str, Any] = {
        "type": _CROSSREF_TYPE_TO_CSL.get(
            (item.get("type") or "").lower(), "article-journal"
        ),
        "title": _first(item.get("title")),
        "author": authors,
        "issued": _issued(year),
        "container-title": _first(item.get("container-title")),
        "volume": item.get("volume"),
        "issue": item.get("issue"),
        "page": item.get("page"),
        "DOI": normalize_doi(item.get("DOI")),
        "URL": item.get("URL"),
        "publisher": item.get("publisher"),
        "ISSN": _first(item.get("ISSN")),
        "custom": _prune(
            {
                "providers": _provider_facts(
                    "crossref",
                    crossref_score=item.get("score"),
                    citation_count=item.get("is-referenced-by-count"),
                ),
            }
        ),
    }
    return _prune(csl)


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value
