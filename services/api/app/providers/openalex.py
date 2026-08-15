"""OpenAlex adapter — search, singleton lookup, and one-hop citation-graph expansion.

Three things about OpenAlex shape this file, and all three are easy to get wrong:

1. **There is no abstract field.** OpenAlex returns `abstract_inverted_index`, a
   `{token: [positions]}` map, and you must invert it. `invert_abstract()` is step two of
   the mandatory fallback chain and the reason a paper S2 has no licence for is still
   reviewable.

2. **It is credit-metered, not request-metered.** 1 credit for a singleton, 10 for a list
   query, 100 for content, 1000 for vector — against 100k/day on a free key. So the
   expensive move here is not "too many requests", it is *one list query per seed*. Graph
   expansion ORs up to 50 ids into a single filter, turning fifty 10-credit queries into
   one.

3. **The two citation filters are easy to invert.** Confirmed against the docs:
   `cites:W123` returns works that **cite** W123 (forward citations); `cited_by:W123`
   returns works in W123's **referenced_works** (its bibliography). Swapping them yields
   plausible-looking, entirely wrong candidates.

`mailto` goes on every call for the polite pool, and is enforced at construction with the
same severity as the key: traffic outside the polite pool is throttled into sparse
results, which is the same invisible false negative a missing key produces (ADR-010).
"""

from __future__ import annotations

from typing import Any

from app.core.contracts import AbstractSource, SourceRecord
from app.core.errors import ConfigurationError
from app.providers.cache import TTL, ResponseCache
from app.providers.csl import openalex_work_to_csl
from app.providers.http import ProviderHTTP, ProviderResponse, RetryPolicy
from app.providers.identity import (
    external_url_for,
    extract_identifiers,
    mint_source_id,
    normalize_doi,
    normalize_openalex_id,
)
from app.providers.keys import require_key, require_mailto
from app.providers.ratelimit import (
    OPENALEX_FREE_DAILY_CREDITS,
    CreditBudget,
    OpenAlexCost,
    TokenBucket,
)

__all__ = ["OpenAlexProvider", "invert_abstract", "OPENALEX_BASE", "MAX_OR_VALUES"]

OPENALEX_BASE = "https://api.openalex.org"

#: OpenAlex caps OR'd filter values at 50. Chunking at this size is what keeps graph
#: expansion at 10 credits per 50 seeds instead of 10 credits per seed.
MAX_OR_VALUES = 50

#: Well under the documented 100 rps hard limit. We are credit-bound long before we are
#: request-bound, so there is nothing to gain from running closer to it.
OPENALEX_REQUESTS_PER_SECOND = 8.0


def invert_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Reconstruct plain text from OpenAlex's `abstract_inverted_index`.

    The index is `{token: [positions]}` — a token appears once per position it occupies.
    Positions can be sparse (OpenAlex drops some tokens), so we sort the positions we
    have rather than indexing into a preallocated list, which would leave holes or throw.

    Returns `None` for a missing or empty index, which the caller turns into
    `AbstractSource.UNAVAILABLE` — a displayable outcome, not an error (ADR-006).
    """
    if not inverted_index:
        return None
    positions: dict[int, str] = {}
    for token, locations in inverted_index.items():
        for location in locations or []:
            if isinstance(location, int):
                positions[location] = token
    if not positions:
        return None
    text = " ".join(positions[i] for i in sorted(positions))
    return text.strip() or None


class OpenAlexProvider:
    """Adapter for the OpenAlex Works API.

    Raises `MissingAPIKeyError` at construction if the key or the polite-pool contact
    address is absent (HR-2). Keys became mandatory on 2026-02-13; there is no anonymous
    tier worth having — 100 credits/day is ten list queries.
    """

    name = "openalex"

    def __init__(
        self,
        *,
        api_key: str | None,
        mailto: str | None,
        cache: ResponseCache,
        store: Any,
        limiter: TokenBucket | None = None,
        budget: CreditBudget | None = None,
        daily_credits: int = OPENALEX_FREE_DAILY_CREDITS,
        enable_vector_search: bool = False,
        vector_endpoint: str | None = None,
        client: Any = None,
    ) -> None:
        # First two statements in the constructor, deliberately. HR-2 / ADR-010.
        self._api_key = require_key(
            api_key, env_var="OPENALEX_API_KEY", provider="OpenAlexProvider"
        )
        self._mailto = require_mailto(
            mailto, env_var="OPENALEX_MAILTO", provider="OpenAlexProvider"
        )
        if store is None:
            raise ConfigurationError(
                "OpenAlexProvider requires a source_store: every record it returns is "
                "written there first, so a source_id always refers to something a real "
                "response produced (HR-1)."
            )
        self.store = store
        self.budget = budget or CreditBudget(daily_limit=daily_credits, name="openalex")
        # Vector search is 1000 credits — 1% of a day's allowance per call. Off unless
        # explicitly enabled *and* explicitly pointed at an endpoint.
        self.enable_vector_search = enable_vector_search
        self.vector_endpoint = vector_endpoint

        self.http = ProviderHTTP(
            provider="openalex",
            base_url=OPENALEX_BASE,
            cache=cache,
            limiter=limiter or TokenBucket(OPENALEX_REQUESTS_PER_SECOND, name="openalex"),
            # The key goes in the query string, which is how OpenAlex documents it.
            # `mailto` rides along on every call, and also in the User-Agent, because the
            # docs accept either and belt-and-braces costs nothing here.
            auth_params={"api_key": self._api_key, "mailto": self._mailto},
            budget=self.budget,
            user_agent=f"answerthat/0.1 (grounded peer review; mailto:{self._mailto})",
            timeout_s=45.0,
            retry=RetryPolicy(max_attempts=4),
            client=client,
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------ Provider protocol

    async def search_works(self, query: str, limit: int = 10) -> list[SourceRecord]:
        response = await self.http.get_json(
            "/works",
            params={"search": query, "per_page": min(limit, 200)},
            ttl_s=TTL.SEARCH,
            credits=OpenAlexCost.LIST,
        )
        return await self._store_works(response.body.get("results") or [], response)

    async def match_reference(self, title: str, year: int | None = None) -> SourceRecord | None:
        """Title-based resolution, used by B1's arbiter after Crossref and S2.

        `title.search` rather than the general `search` field: the general one also
        matches the abstract, which resolves a reference to a paper that merely *mentions*
        it. That is precisely the plausible-but-wrong match the arbiter's agreement score
        exists to catch, and not generating it is cheaper than scoring it away.
        """
        filters = [f"title.search:{_escape_filter(title)}"]
        if year:
            filters.append(f"publication_year:{year}")
        response = await self.http.get_json(
            "/works",
            params={"filter": ",".join(filters), "per_page": 5},
            ttl_s=TTL.MATCH,
            credits=OpenAlexCost.LIST,
        )
        works = response.body.get("results") or []
        if not works:
            return None
        records = await self._store_works(works[:1], response)
        return records[0] if records else None

    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]:
        """Step two of the fallback chain: OpenAlex's inverted index, inverted.

        A singleton lookup (1 credit), not a list query (10), because we have an id.
        """
        record = await self.store.fetch(source_id)
        if record is None:
            raise KeyError(
                f"source_id {source_id!r} is not in the source store. Abstracts are only "
                "fetched for records a provider already wrote (HR-1)."
            )
        ids = extract_identifiers(record.csl)
        ref = ids.openalex_id or (f"doi:{ids.doi}" if ids.doi else None)
        if ref is None:
            return None, AbstractSource.UNAVAILABLE

        response = await self.http.get_json_or_none(
            f"/works/{ref}", ttl_s=TTL.RECORD, credits=OpenAlexCost.SINGLETON
        )
        if response is None:
            return None, AbstractSource.UNAVAILABLE

        abstract = invert_abstract(response.body.get("abstract_inverted_index"))
        if not abstract:
            return None, AbstractSource.UNAVAILABLE

        await self._enrich_abstract(record, response, abstract)
        return abstract, AbstractSource.OPENALEX_INVERTED

    async def batch_hydrate(self, ids: list[str]) -> list[SourceRecord]:
        """Hydrate works by id, 50 per list query rather than one query each.

        Accepts our own `src_…` ids or OpenAlex-native `W…` ids and bare DOIs. Fifty ids
        in one 10-credit query instead of fifty 1-credit singletons is a wash on credits
        and a 50x saving on wall-clock, which is what actually binds during a review.
        """
        native = await self._resolve_refs(ids)
        if not native:
            return []

        openalex_ids = sorted({i for i in native if i.startswith("W")})
        dois = sorted({i for i in native if not i.startswith("W")})

        records: list[SourceRecord] = []
        for chunk in _chunks(openalex_ids, MAX_OR_VALUES):
            records.extend(await self._filter_query(f"openalex_id:{'|'.join(chunk)}"))
        for chunk in _chunks(dois, MAX_OR_VALUES):
            records.extend(await self._filter_query(f"doi:{'|'.join(chunk)}"))
        return records

    # ------------------------------------------------------------------ graph expansion

    async def citing_works(self, openalex_ids: list[str], *, limit: int = 50) -> list[SourceRecord]:
        """Works that **cite** the seeds — `cites:` (forward citations).

        For review this is "who built on the work this paper cites?", which is where a
        missed follow-up paper lives.
        """
        seeds = _clean_ids(openalex_ids)
        records: list[SourceRecord] = []
        for chunk in _chunks(seeds, MAX_OR_VALUES):
            records.extend(
                await self._filter_query(f"cites:{'|'.join(chunk)}", per_page=limit)
            )
        return records

    async def referenced_works(
        self, openalex_ids: list[str], *, limit: int = 50
    ) -> list[SourceRecord]:
        """Works the seeds **cite** — `cited_by:` (the seeds' own bibliographies).

        For review this is "what did the papers they cited consider essential?", which is
        where a missed foundational paper lives.
        """
        seeds = _clean_ids(openalex_ids)
        records: list[SourceRecord] = []
        for chunk in _chunks(seeds, MAX_OR_VALUES):
            records.extend(
                await self._filter_query(f"cited_by:{'|'.join(chunk)}", per_page=limit)
            )
        return records

    async def one_hop_expansion(
        self, openalex_ids: list[str], *, limit: int = 50
    ) -> list[SourceRecord]:
        """Both directions from the existing bibliography, as ADR-005 strategy 3.

        Two list queries per 50 seeds, not two per seed.
        """
        forward = await self.citing_works(openalex_ids, limit=limit)
        backward = await self.referenced_works(openalex_ids, limit=limit)
        merged: dict[str, SourceRecord] = {r.source_id: r for r in forward}
        for record in backward:
            merged.setdefault(record.source_id, record)
        return list(merged.values())

    async def semantic_search(self, query: str, *, limit: int = 20) -> list[SourceRecord]:
        """OpenAlex vector search. Off by default, and budget-gated when on.

        1000 credits per call — 1% of a free key's day. It stays disabled unless an
        operator both enables it and names the endpoint, and it refuses to run once the
        remaining budget would dip into the reserve that keeps abstract hydration alive.
        """
        if not self.enable_vector_search or not self.vector_endpoint:
            raise ConfigurationError(
                "OpenAlex vector search is disabled. It costs 1000 credits per call — "
                "100x a list query — so it is opt-in via `enable_vector_search=True` plus "
                "an explicit `vector_endpoint`. The three strategies in ADR-005 do not "
                "need it."
            )
        if not self.budget.can_afford(OpenAlexCost.VECTOR):
            raise ConfigurationError(
                f"OpenAlex vector search needs {int(OpenAlexCost.VECTOR)} credits but only "
                f"{self.budget.remaining} remain above the reserve. Refusing rather than "
                "spending the reserve that abstract hydration depends on."
            )
        response = await self.http.get_json(
            self.vector_endpoint,
            params={"q": query, "per_page": limit},
            ttl_s=TTL.SEARCH,
            credits=OpenAlexCost.VECTOR,
        )
        return await self._store_works(response.body.get("results") or [], response)

    # ------------------------------------------------------------------ internals

    async def _filter_query(self, filter_expr: str, *, per_page: int = 50) -> list[SourceRecord]:
        response = await self.http.get_json(
            "/works",
            params={"filter": filter_expr, "per_page": min(per_page, 200)},
            ttl_s=TTL.GRAPH,
            credits=OpenAlexCost.LIST,
        )
        return await self._store_works(response.body.get("results") or [], response)

    async def _resolve_refs(self, ids: list[str]) -> list[str]:
        native: list[str] = []
        ours = [i for i in ids if i and i.startswith("src_")]
        if ours:
            await self.store.warm(ours)
        for identifier in ids:
            if not identifier:
                continue
            if identifier.startswith("src_"):
                record = self.store.get(identifier)
                if record is None:
                    continue
                work_ids = extract_identifiers(record.csl)
                if work_ids.openalex_id:
                    native.append(work_ids.openalex_id)
                elif work_ids.doi:
                    native.append(work_ids.doi)
                continue
            openalex_id = normalize_openalex_id(identifier)
            if openalex_id:
                native.append(openalex_id)
                continue
            doi = normalize_doi(identifier)
            if doi:
                native.append(doi)
        return native

    async def _store_works(
        self, works: list[dict], response: ProviderResponse
    ) -> list[SourceRecord]:
        """Write every work to the append-only store. The only place OpenAlex data becomes
        a `source_id`, and it happens with provenance minted from this response. HR-1."""
        records: list[SourceRecord] = []
        for work in works:
            record = self._to_record(work, response)
            if record is None:
                continue
            await self.store.put(record)
            records.append(self.store.get(record.source_id) or record)
        return records

    def _to_record(self, work: dict, response: ProviderResponse) -> SourceRecord | None:
        if not work or not (work.get("title") or work.get("display_name") or "").strip():
            return None
        csl = openalex_work_to_csl(work)
        ids = extract_identifiers(csl)
        try:
            source_id = mint_source_id(ids)
            external_url = external_url_for(
                ids, fallback=(work.get("primary_location") or {}).get("landing_page_url")
            )
        except ValueError:
            return None

        abstract = invert_abstract(work.get("abstract_inverted_index"))
        return SourceRecord(
            source_id=source_id,
            csl=csl,
            provenance=response.provenance(external_url),
            abstract=abstract,
            abstract_source=(
                AbstractSource.OPENALEX_INVERTED if abstract else AbstractSource.UNAVAILABLE
            ),
        )

    async def _enrich_abstract(
        self, record: SourceRecord, response: ProviderResponse, abstract: str
    ) -> None:
        ids = extract_identifiers(record.csl)
        await self.store.put(
            SourceRecord(
                source_id=record.source_id,
                csl=record.csl,
                provenance=response.provenance(
                    external_url_for(ids, fallback=record.provenance.external_url)
                ),
                abstract=abstract,
                abstract_source=AbstractSource.OPENALEX_INVERTED,
            )
        )

    def snapshot(self) -> dict[str, Any]:
        return self.http.snapshot()


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def _clean_ids(openalex_ids: list[str]) -> list[str]:
    seen = {normalize_openalex_id(i) for i in openalex_ids}
    return sorted(i for i in seen if i)


def _escape_filter(value: str) -> str:
    """Strip the characters that are filter syntax in OpenAlex.

    Commas separate AND clauses and pipes separate OR values, so a title containing one
    silently becomes a different query. Colons terminate the filter key.
    """
    return " ".join(value.replace(",", " ").replace("|", " ").replace(":", " ").split())
