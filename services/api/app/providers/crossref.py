"""Crossref adapter — DOI resolution and bibliographic matching.

Crossref is first in the arbiter's cascade (ADR-001) for one reason: when a parsed
reference carries a DOI, Crossref answers definitively and cheaply, and no amount of
title matching beats a DOI lookup. It is also where CSL-JSON came from, so the mapping is
the shallowest of the three — though still explicit, because Crossref returns `title` and
`container-title` as *arrays*, and a citeproc handed an array where it expects a string
renders wrongly rather than failing (HR-4).

Crossref has no API key. It has a polite pool, entered by sending a contact address, and
being outside it means throttling that presents as sparse results — the same invisible
failure a missing key produces. So `mailto` is required here with the same severity, via
the same `MissingAPIKeyError`. That is deliberate consistency, not an oversight about
which credentials HR-2 names.
"""

from __future__ import annotations

from typing import Any

from app.core.contracts import AbstractSource, SourceRecord
from app.core.errors import ConfigurationError
from app.providers.cache import TTL, ResponseCache
from app.providers.csl import crossref_item_to_csl
from app.providers.http import ProviderHTTP, ProviderResponse, RetryPolicy
from app.providers.identity import (
    external_url_for,
    extract_identifiers,
    mint_source_id,
    normalize_doi,
)
from app.providers.keys import require_mailto
from app.providers.ratelimit import TokenBucket

__all__ = ["CrossrefProvider", "CROSSREF_BASE"]

CROSSREF_BASE = "https://api.crossref.org"

#: Crossref publishes no hard number for the polite pool; this is conservative enough to
#: stay inside any of the published guidance and we are not latency-bound here.
CROSSREF_REQUESTS_PER_SECOND = 5.0


class CrossrefProvider:
    """Adapter for the Crossref REST API."""

    name = "crossref"

    def __init__(
        self,
        *,
        mailto: str | None,
        cache: ResponseCache,
        store: Any,
        limiter: TokenBucket | None = None,
        client: Any = None,
    ) -> None:
        self._mailto = require_mailto(
            mailto, env_var="OPENALEX_MAILTO", provider="CrossrefProvider"
        )
        if store is None:
            raise ConfigurationError(
                "CrossrefProvider requires a source_store: every record it returns is "
                "written there first, so a source_id always refers to something a real "
                "response produced (HR-1)."
            )
        self.store = store
        self.http = ProviderHTTP(
            provider="crossref",
            base_url=CROSSREF_BASE,
            cache=cache,
            limiter=limiter or TokenBucket(CROSSREF_REQUESTS_PER_SECOND, name="crossref"),
            auth_params={"mailto": self._mailto},
            user_agent=f"answerthat/0.1 (grounded peer review; mailto:{self._mailto})",
            timeout_s=30.0,
            retry=RetryPolicy(max_attempts=4),
            client=client,
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------ Provider protocol

    async def search_works(self, query: str, limit: int = 10) -> list[SourceRecord]:
        """`query.bibliographic` rather than the general `query`.

        The bibliographic index is built for matching a whole reference string — author,
        title, year, venue together — which is what we actually hold. The general index
        matches any field and returns topical neighbours.
        """
        response = await self.http.get_json(
            "/works",
            params={"query.bibliographic": query, "rows": min(limit, 100)},
            ttl_s=TTL.SEARCH,
        )
        items = (response.body.get("message") or {}).get("items") or []
        return await self._store_items(items, response)

    async def match_reference(self, title: str, year: int | None = None) -> SourceRecord | None:
        params: dict[str, Any] = {"query.bibliographic": title, "rows": 3}
        if year:
            # Crossref's filter syntax, not a free-text hint.
            params["filter"] = f"from-pub-date:{year}-01-01,until-pub-date:{year}-12-31"
        response = await self.http.get_json("/works", params=params, ttl_s=TTL.MATCH)
        items = (response.body.get("message") or {}).get("items") or []
        if not items:
            return None
        records = await self._store_items(items[:1], response)
        return records[0] if records else None

    async def resolve_doi(self, doi: str) -> SourceRecord | None:
        """The arbiter's first move when a parsed reference already carries a DOI.

        A 404 means the DOI does not exist at Crossref — a real answer, and grounds for
        distrusting the parse. Returned as `None`, never raised into a swallowed empty.
        """
        normalized = normalize_doi(doi)
        if not normalized:
            return None
        response = await self.http.get_json_or_none(f"/works/{normalized}", ttl_s=TTL.RECORD)
        if response is None:
            return None
        item = (response.body.get("message") or {}) if isinstance(response.body, dict) else {}
        if not item:
            return None
        records = await self._store_items([item], response)
        return records[0] if records else None

    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]:
        """Crossref is not part of the abstract fallback chain.

        Its `abstract` field is JATS XML, present for a minority of records, and the
        chain in ADR-006 is defined as S2 → OpenAlex inverted → S2 TLDR → unavailable.
        Returning `UNAVAILABLE` here is the accurate answer rather than a placeholder:
        this provider does not supply abstracts to this system.
        """
        return None, AbstractSource.UNAVAILABLE

    async def batch_hydrate(self, ids: list[str]) -> list[SourceRecord]:
        """Crossref has no batch endpoint, so this resolves DOIs one at a time.

        Kept honest rather than convenient: the name promises a batch and the API cannot
        give one, so callers hydrating many works should prefer S2's `/paper/batch`, which
        takes 500 in a call. This exists so the `Provider` protocol is satisfied for the
        DOI-resolution path, not as a bulk loader.
        """
        records: list[SourceRecord] = []
        ours = [i for i in ids if i and i.startswith("src_")]
        if ours:
            await self.store.warm(ours)
        for identifier in ids:
            if not identifier:
                continue
            if identifier.startswith("src_"):
                stored = self.store.get(identifier)
                doi = extract_identifiers(stored.csl).doi if stored else None
            else:
                doi = normalize_doi(identifier)
            if not doi:
                continue
            record = await self.resolve_doi(doi)
            if record is not None:
                records.append(record)
        return records

    # ------------------------------------------------------------------ internals

    async def _store_items(
        self, items: list[dict], response: ProviderResponse
    ) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for item in items:
            record = self._to_record(item, response)
            if record is None:
                continue
            await self.store.put(record)
            records.append(self.store.get(record.source_id) or record)
        return records

    def _to_record(self, item: dict, response: ProviderResponse) -> SourceRecord | None:
        csl = crossref_item_to_csl(item)
        if not (csl.get("title") or "").strip():
            return None
        ids = extract_identifiers(csl)
        try:
            source_id = mint_source_id(ids)
            external_url = external_url_for(ids, fallback=item.get("URL"))
        except ValueError:
            return None
        return SourceRecord(
            source_id=source_id,
            csl=csl,
            provenance=response.provenance(external_url),
            # Crossref abstracts are JATS XML and only sometimes present; the chain does
            # not use them, so nothing is claimed here.
            abstract=None,
            abstract_source=AbstractSource.UNAVAILABLE,
        )

    def snapshot(self) -> dict[str, Any]:
        return self.http.snapshot()
