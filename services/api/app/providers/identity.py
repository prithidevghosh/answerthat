"""Canonical identity for retrieved works: `source_id` minting and dedupe keys.

Two related but distinct jobs, and conflating them causes a subtle bug:

* **`source_id`** is the primary key of the append-only store. It must be *deterministic*
  so that re-running review over the same paper reuses the same rows rather than growing
  a parallel set, and so that the store's append-only enrichment rule (an abstract
  arriving later) has something to enrich.
* **`dedupe_key`** is what fusion collapses on. It must be *cross-provider*: the same
  paper found by S2 with a DOI and by OpenAlex with the same DOI has to collapse to one
  candidate, or the reciprocal-rank fusion double-counts it and it outranks everything.

They differ because a work without a DOI gets a provider-scoped `source_id` (two real
records, both legitimately stored) but should still collapse at fusion time on a
normalized title+year. That is deliberate: the store is a record of what we fetched, and
fusion is a view over it.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

__all__ = [
    "normalize_doi",
    "normalize_title",
    "extract_identifiers",
    "mint_source_id",
    "dedupe_key",
    "external_url_for",
    "WorkIdentifiers",
]

_DOI_PREFIX = re.compile(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:)\s*", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")
_OPENALEX_ID = re.compile(r"(W\d+)\s*$", re.IGNORECASE)


def normalize_doi(value: str | None) -> str | None:
    """Bare, lowercased DOI, or None. Accepts a full doi.org URL or a `doi:` prefix."""
    if not value:
        return None
    doi = _DOI_PREFIX.sub("", str(value)).strip().rstrip(".").lower()
    return doi if doi.startswith("10.") and "/" in doi else None


def normalize_title(value: str | None) -> str:
    """Aggressively normalized title for equality comparison only.

    NFKD-folded, punctuation stripped, whitespace collapsed. Never display this — it is a
    comparison key, and a lossy one on purpose.
    """
    if not value:
        return ""
    folded = unicodedata.normalize("NFKD", str(value))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return _WS.sub(" ", _PUNCT.sub(" ", folded.casefold())).strip()


def normalize_openalex_id(value: str | None) -> str | None:
    """`https://openalex.org/W2741809807` or `W2741809807` -> `W2741809807`."""
    if not value:
        return None
    match = _OPENALEX_ID.search(str(value).strip())
    return match.group(1).upper() if match else None


class WorkIdentifiers:
    """Whatever identifiers a provider gave us for one work."""

    __slots__ = ("doi", "s2_paper_id", "s2_corpus_id", "openalex_id", "arxiv_id", "pmid", "title", "year")

    def __init__(
        self,
        *,
        doi: str | None = None,
        s2_paper_id: str | None = None,
        s2_corpus_id: str | None = None,
        openalex_id: str | None = None,
        arxiv_id: str | None = None,
        pmid: str | None = None,
        title: str | None = None,
        year: int | None = None,
    ) -> None:
        self.doi = normalize_doi(doi)
        self.s2_paper_id = (s2_paper_id or "").strip() or None
        self.s2_corpus_id = str(s2_corpus_id).strip() if s2_corpus_id else None
        self.openalex_id = normalize_openalex_id(openalex_id)
        self.arxiv_id = (arxiv_id or "").strip() or None
        self.pmid = (pmid or "").strip() or None
        self.title = title
        self.year = year

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"WorkIdentifiers(doi={self.doi!r}, s2={self.s2_paper_id!r}, "
            f"openalex={self.openalex_id!r}, title={(self.title or '')[:40]!r})"
        )


def extract_identifiers(csl: dict[str, Any]) -> WorkIdentifiers:
    """Pull identifiers back out of a CSL-JSON record.

    Adapters store provider ids under CSL's `custom` extension namespace rather than
    inventing top-level CSL fields — CSL-JSON is the one citation model (HR-4) and we do
    not get to extend its schema.
    """
    custom = csl.get("custom") or {}
    issued = csl.get("issued") or {}
    parts = (issued.get("date-parts") or [[None]])[0]
    year = parts[0] if parts and isinstance(parts[0], int) else None
    return WorkIdentifiers(
        doi=csl.get("DOI"),
        s2_paper_id=custom.get("s2_paper_id"),
        s2_corpus_id=custom.get("s2_corpus_id"),
        openalex_id=custom.get("openalex_id"),
        arxiv_id=custom.get("arxiv_id"),
        pmid=custom.get("pmid"),
        title=csl.get("title"),
        year=year,
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def mint_source_id(ids: WorkIdentifiers) -> str:
    """Deterministic `source_id`, most-authoritative identifier first.

    Preference order matters: a DOI is global, so two providers returning the same DOI
    produce the same `source_id` and the second `put()` becomes an enrichment of the
    first rather than a duplicate row. Provider-local ids only win when no DOI exists.

    Falls back to normalized title+year, and raises if even that is empty — a record with
    no DOI, no provider id and no title is not something we can key, store, or show a
    reader, and inventing a random id for it would put an unverifiable row in the store.
    """
    if ids.doi:
        return f"src_{_digest('doi:' + ids.doi)}"
    if ids.openalex_id:
        return f"src_{_digest('openalex:' + ids.openalex_id)}"
    if ids.s2_paper_id:
        return f"src_{_digest('s2:' + ids.s2_paper_id)}"
    if ids.s2_corpus_id:
        return f"src_{_digest('s2corpus:' + ids.s2_corpus_id)}"
    if ids.arxiv_id:
        return f"src_{_digest('arxiv:' + ids.arxiv_id)}"
    if ids.pmid:
        return f"src_{_digest('pmid:' + ids.pmid)}"
    title = normalize_title(ids.title)
    if title:
        year = ids.year or ""
        return f"src_{_digest(f'title:{title}|{year}')}"
    raise ValueError(
        "cannot mint a source_id: the record has no DOI, no provider id and no title. "
        "Refusing to generate an arbitrary id — an unkeyable record cannot be verified "
        "by a reader and must not enter the source store (HR-1)."
    )


def dedupe_key(ids: WorkIdentifiers) -> str:
    """Cross-provider collapse key for fusion.

    DOI when present — it is the only identifier both providers share. Otherwise
    normalized title plus year, which catches the same preprint surfaced by S2 and
    OpenAlex under different local ids. Falls back to the provider id, which collapses
    nothing but at least never merges two distinct works.
    """
    if ids.doi:
        return f"doi:{ids.doi}"
    title = normalize_title(ids.title)
    if title:
        return f"title:{title}|{ids.year or ''}"
    for prefix, value in (
        ("openalex", ids.openalex_id),
        ("s2", ids.s2_paper_id),
        ("arxiv", ids.arxiv_id),
        ("pmid", ids.pmid),
    ):
        if value:
            return f"{prefix}:{value}"
    return "unkeyable"


def external_url_for(ids: WorkIdentifiers, fallback: str | None = None) -> str:
    """The public landing page a reader clicks to check we did not invent the source.

    DOI first because it is the stable, publisher-independent one. `fallback` is the
    provider's own `url` field where it supplied one.
    """
    if ids.doi:
        return f"https://doi.org/{ids.doi}"
    if ids.openalex_id:
        return f"https://openalex.org/{ids.openalex_id}"
    if ids.arxiv_id:
        return f"https://arxiv.org/abs/{ids.arxiv_id}"
    if ids.s2_paper_id:
        return f"https://www.semanticscholar.org/paper/{ids.s2_paper_id}"
    if ids.pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{ids.pmid}/"
    if fallback:
        return fallback
    raise ValueError(
        "cannot build a resolvable external_url for this record. Provenance requires one "
        "(HR-1) — a finding a reader cannot open is indistinguishable from a fabricated one."
    )
