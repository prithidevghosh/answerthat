"""Semantic Scholar adapter — search, title match, batch hydration, snippets, recommendations.

Implements the `Provider` protocol from Appendix A, plus the two endpoints the review
pipeline's candidate generation depends on (ADR-005):

* `/snippet/search` — passage-level evidence, the only endpoint either provider offers
  that returns *text supporting a claim* rather than a title that is merely on-topic.
* Recommendations, seeded with the paper's own cited works — the SPECTER2 embedding
  space, and the only genuinely semantic signal available to us. Seeding it with the
  bibliography is the direct expression of "what did this literature neighbourhood
  contain that they missed?".

Operating constraints that shape the whole file:

* **~1 request/second.** Everything batches or queues. `/paper/batch` hydrates up to 500
  ids in one call, so nothing here loops over ids. With a key that 1 RPS is ours; without
  one we are in a pool shared with every other unauthenticated client, so the same budget
  is nominally far larger but contended and bursty. Either way the answer is to batch.
* **The key is optional (ADR-010a).** Present, it is sent as `x-api-key` and buys the
  dedicated allowance. Absent, we call anonymously — throttling then arrives as HTTP 429,
  which `ProviderHTTP` raises rather than turning into an empty result, so an
  unauthenticated review cannot silently report "no missing work found".
* **Unauthenticated, the search pool is closed, not slow.** ADR-010a expected anonymous
  access to be contended and bursty. Measured, `/snippet/search` and `/paper/search`
  answer 429 to a single cold request while title matching, recommendations and graph
  traversal answer normally — see `SEARCH_POOL_ENDPOINTS`. Methods on that pool are
  gated by `search_pool_available` and raise `ProviderEndpointUnavailable` up front,
  so callers drop the strategy deliberately instead of losing a review to an exception.
* **Abstracts are missing for a meaningful fraction of records** because of publisher
  licensing. That is why `get_abstract` exists and why `unavailable` is a real outcome.

Every record this adapter returns has already been written to the append-only
`source_store` with provenance from the response that produced it. Callers receive
`SourceRecord`s whose `source_id` is a key that already exists — they never mint one.
"""

from __future__ import annotations

from typing import Any

from app.core.contracts import AbstractSource, SourceRecord
from app.core.errors import ConfigurationError
from app.providers.cache import TTL, ResponseCache
from app.providers.csl import s2_paper_to_csl
from app.providers.errors import ProviderEndpointUnavailable
from app.providers.http import ProviderHTTP, ProviderResponse, RetryPolicy
from app.providers.identity import (
    WorkIdentifiers,
    external_url_for,
    extract_identifiers,
    mint_source_id,
    normalize_doi,
)
from app.providers.keys import optional_key
from app.providers.ratelimit import S2_REQUESTS_PER_SECOND, TokenBucket

__all__ = ["SemanticScholarProvider", "Snippet", "S2_FIELDS", "S2_RECOMMENDATION_FIELDS"]

GRAPH_BASE = "https://api.semanticscholar.org/graph/v1"
RECOMMENDATIONS_BASE = "https://api.semanticscholar.org/recommendations/v1"

# Requested on every paper-shaped call. `tldr` is here because it is step three of the
# abstract fallback chain; asking for it later would cost another rate-limited request.
S2_FIELDS = ",".join(
    [
        "paperId",
        "corpusId",
        "externalIds",
        "url",
        "title",
        "abstract",
        "venue",
        "publicationVenue",
        "year",
        "authors",
        "journal",
        "publicationTypes",
        "tldr",
        "citationCount",
        "referenceCount",
        "openAccessPdf",
    ]
)

#: The Recommendations API is a *different service* from the Graph API and accepts a
#: narrower field list: `tldr` is a Graph-only field, and asking for it there is not a
#: degraded answer but a hard `400 Unrecognized or unsupported fields: [tldr]`.
#:
#: That 400 killed the whole review. `s2_recommendations` is the bibliography-seeded
#: strategy (CP-3), it runs on the first claim, and the exception propagated out of the
#: streaming runner — so a paper got nine claims extracted, zero verified, and a terminal
#: error before a single finding. The abstract chain loses nothing by it: a recommended
#: paper with no `abstract` falls to OpenAlex and then to a Graph-side `tldr` lookup on
#: the record itself (`abstracts.py`), which is where step three of that chain lives.
S2_RECOMMENDATION_FIELDS = ",".join(f for f in S2_FIELDS.split(",") if f != "tldr")

#: `/paper/batch` accepts up to 500 ids per call.
BATCH_LIMIT = 500

#: S2 serves *search and batch* from a different pool than the rest of the Graph API.
#: With a key that pool is a dedicated ~1 rps. Without one it is shared with every other
#: unauthenticated client on the internet, and it is not merely slower — it is closed.
#: Measured 2026-08-16 from an idle machine, six calls per endpoint two seconds apart:
#:
#:     /snippet/search             0/6      /paper/search/match          6/6
#:     /paper/search               0/6      /paper/{id}/references       6/6
#:     /paper/batch                1/6      /paper/{id}/citations        5/6
#:     /paper/{id}                 1/6      /recommendations/v1/papers   6/6
#:
#: A *single cold request* to `/snippet/search` answers 429, so the usual remedies do not
#: apply: this is not burstiness a token bucket can smooth, and ADR-010a's "or a lower
#: request rate" cannot reach zero. Retrying costs four attempts and ~7s of backoff — S2
#: sends no `Retry-After` — and then raises, which is the failure that killed a whole
#: review at the first claim.
#:
#: The right-hand column is why an unauthenticated review is still worth running: title
#: matching, recommendations and graph traversal all answer normally, so the arbiter and
#: two of ADR-005's three candidate strategies are unaffected.
SEARCH_POOL_ENDPOINTS = frozenset(
    {"/snippet/search", "/paper/search", "/paper/batch", "/paper/{id}"}
)


class Snippet:
    """One passage-level hit from `/snippet/search`.

    The `text` is real retrieved text, not a model's paraphrase, which is what makes it
    usable as evidence rather than as a hint.
    """

    __slots__ = ("text", "kind", "section", "score", "source_id", "record")

    def __init__(
        self,
        *,
        text: str,
        kind: str,
        section: str | None,
        score: float,
        source_id: str,
        record: SourceRecord | None = None,
    ) -> None:
        self.text = text
        self.kind = kind
        self.section = section
        self.score = score
        self.source_id = source_id
        self.record = record

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Snippet({self.source_id}, {self.kind}, {self.text[:60]!r})"


class SemanticScholarProvider:
    """Adapter for the Semantic Scholar Academic Graph and Recommendations APIs.

    Constructs with or without a key (ADR-010a). There is still no anonymous constructor
    argument to find — authentication is not a mode a caller selects, it follows from
    whether `api_key` holds a credential. `authenticated` reports which regime we are in
    so that startup and `/health` can say so out loud; nothing in the request path
    branches on it.
    """

    name = "semantic_scholar"

    def __init__(
        self,
        *,
        api_key: str | None,
        cache: ResponseCache,
        store: Any,
        limiter: TokenBucket | None = None,
        rate_per_sec: float = S2_REQUESTS_PER_SECOND,
        client: Any = None,
        recommendations_client: Any = None,
    ) -> None:
        # ADR-010a: optional. S2 answers a throttled anonymous call with 429, which
        # `ProviderHTTP` raises — the failure is loud, so a startup gate would buy no
        # safety and would lock out operators S2 will not issue a key to.
        self._api_key = optional_key(api_key)
        if store is None:
            raise ConfigurationError(
                "SemanticScholarProvider requires a source_store: every record it returns "
                "is written there first, so that a source_id always refers to something a "
                "real response produced (HR-1)."
            )
        self.store = store
        # One limiter shared by both base URLs: the ~1 rps allowance is per key, not per
        # host, and two independent buckets would quietly double our request rate.
        self.limiter = limiter or TokenBucket(rate_per_sec, name="semantic_scholar")
        # Omitted entirely when unauthenticated. An `x-api-key: ""` header is not the same
        # request as no header at all, and sending one is a 403 waiting to happen.
        headers = {"x-api-key": self._api_key} if self._api_key else {}
        user_agent = "answerthat/0.1 (grounded peer review; +https://github.com/answerthat)"

        self.graph = ProviderHTTP(
            provider="semantic_scholar",
            base_url=GRAPH_BASE,
            cache=cache,
            limiter=self.limiter,
            auth_headers=headers,
            user_agent=user_agent,
            timeout_s=45.0,
            retry=RetryPolicy(max_attempts=4),
            client=client,
        )
        self.recommendations = ProviderHTTP(
            provider="semantic_scholar",
            base_url=RECOMMENDATIONS_BASE,
            cache=cache,
            limiter=self.limiter,
            auth_headers=headers,
            user_agent=user_agent,
            timeout_s=45.0,
            retry=RetryPolicy(max_attempts=4),
            client=recommendations_client or client,
        )

    @property
    def authenticated(self) -> bool:
        """Whether calls carry a key. Reported; branched on only via `search_pool_available`."""
        return self._api_key is not None

    @property
    def search_pool_available(self) -> bool:
        """Whether `SEARCH_POOL_ENDPOINTS` can be called at all in this regime.

        ADR-010a said `authenticated` is "reported, never branched on", on the premise
        that anonymous access is "contended and bursty" — slow, but working. Measured,
        that premise does not hold for the search pool: it is at zero, not thin (see
        `SEARCH_POOL_ENDPOINTS`). So there is now exactly one thing the request path
        branches on, and this is it.

        This is a narrow amendment, not a hole. It does not say "return nothing when
        throttled" — a 429 from any endpoint we *do* call still raises, unchanged. It
        says which endpoints are worth calling, so a caller can drop a strategy and
        report that it dropped it, instead of learning the same fact seven seconds later
        as an exception that takes the whole review with it.
        """
        return self.authenticated

    async def aclose(self) -> None:
        await self.graph.aclose()
        await self.recommendations.aclose()

    def _require_search_pool(self, endpoint: str) -> None:
        """Refuse a search-pool call we know has no working answer in this regime."""
        if self.search_pool_available:
            return
        raise ProviderEndpointUnavailable(
            self.name,
            endpoint,
            "no SEMANTIC_SCHOLAR_API_KEY, and unauthenticated this endpoint answers 429 "
            "to a single cold request. Gate on `search_pool_available` and drop the "
            "strategy — calling anyway spends four retries to learn what the "
            "configuration already says.",
        )

    # ------------------------------------------------------------------ Provider protocol

    async def search_works(self, query: str, limit: int = 10) -> list[SourceRecord]:
        """Relevance search. On the search pool, so unauthenticated it is unavailable.

        Nothing in the review or ingest path calls this — the arbiter searches OpenAlex
        and matches titles through `match_reference` — so the gate costs no capability
        today. It is here so that wiring it in later fails at the call site instead of
        producing a review that is quietly one strategy short.
        """
        self._require_search_pool("/paper/search")
        response = await self.graph.get_json(
            "/paper/search",
            params={"query": query, "limit": min(limit, 100), "fields": S2_FIELDS},
            ttl_s=TTL.SEARCH,
        )
        papers = response.body.get("data") or []
        return await self._store_papers(papers, response)

    async def match_reference(self, title: str, year: int | None = None) -> SourceRecord | None:
        """S2's title-matching endpoint, which B1's arbiter resolves references through.

        A 404 here means "no title matched" — a real answer, and the entry condition for
        the `quarantined` tier. It is returned as `None`, not raised, and not turned into
        an empty list that would read like a successful search with no hits.
        """
        params: dict[str, Any] = {"query": title, "fields": S2_FIELDS}
        if year:
            params["year"] = str(year)
        response = await self.graph.get_json_or_none(
            "/paper/search/match", params=params, ttl_s=TTL.MATCH
        )
        if response is None:
            return None
        papers = response.body.get("data") or []
        if not papers:
            return None
        records = await self._store_papers(papers[:1], response)
        return records[0] if records else None

    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]:
        """S2's abstract for a stored record, else its TLDR, else unavailable.

        This is steps one and three of the mandatory fallback chain; `abstracts.py`
        sequences it with OpenAlex in between. `unavailable` is returned, never raised
        and never silently skipped — it is a displayable outcome (ADR-006).

        `/paper/{id}` is on the search pool, so unauthenticated this raises. Note what
        that does *not* mean: it is not `unavailable`, and `AbstractResolver` must not
        record it as one. It means this step of the chain cannot run, so OpenAlex becomes
        the first step that can — which is why the resolver skips rather than catches.
        """
        self._require_search_pool("/paper/{id}")
        record = await self.store.fetch(source_id)
        if record is None:
            raise KeyError(
                f"source_id {source_id!r} is not in the source store. Abstracts are only "
                "fetched for records a provider already wrote (HR-1)."
            )
        ids = extract_identifiers(record.csl)
        paper_ref = _paper_ref(ids)
        if paper_ref is None:
            return None, AbstractSource.UNAVAILABLE

        response = await self.graph.get_json_or_none(
            f"/paper/{paper_ref}",
            params={"fields": "paperId,corpusId,externalIds,url,title,abstract,tldr"},
            ttl_s=TTL.RECORD,
        )
        if response is None:
            return None, AbstractSource.UNAVAILABLE

        paper = response.body
        abstract = (paper.get("abstract") or "").strip()
        if abstract:
            await self._enrich_abstract(record, response, abstract, AbstractSource.S2)
            return abstract, AbstractSource.S2

        tldr = ((paper.get("tldr") or {}).get("text") or "").strip()
        if tldr:
            await self._enrich_abstract(record, response, tldr, AbstractSource.TLDR)
            return tldr, AbstractSource.TLDR

        return None, AbstractSource.UNAVAILABLE

    async def batch_hydrate(self, ids: list[str]) -> list[SourceRecord]:
        """Hydrate up to 500 works per call. Never loop over ids at ~1 rps.

        Accepts either our own `src_…` ids (resolved through the store) or S2-native
        references: a bare `paperId`, `DOI:10.…`, `CorpusId:…`, `ARXIV:…`, `MAG:…`.

        Ids are **sorted before sending** so that the cache key and the request agree —
        `normalize_query` sorts containers, and results are mapped back by paper id
        rather than by position, so a reordered request is a cache hit rather than a
        second identical call.

        On the search pool, so unauthenticated this raises. Its only caller is
        `snippet_search`, which is gated the same way, so in practice the two are dropped
        together rather than one discovering the other's absence.
        """
        self._require_search_pool("/paper/batch")
        refs = await self._resolve_refs(ids)
        if not refs:
            return []

        records: list[SourceRecord] = []
        ordered = sorted(set(refs))
        for start in range(0, len(ordered), BATCH_LIMIT):
            chunk = ordered[start : start + BATCH_LIMIT]
            response = await self.graph.post_json(
                "/paper/batch",
                params={"fields": S2_FIELDS},
                json_body={"ids": chunk},
                ttl_s=TTL.RECORD,
            )
            # `/paper/batch` answers with a bare list, one slot per requested id, `null`
            # where nothing matched. `null`s are dropped here rather than treated as an
            # error — "we asked and S2 has no such record" is a legitimate answer.
            papers = response.body.get("data") if isinstance(response.body, dict) else response.body
            records.extend(await self._store_papers([p for p in (papers or []) if p], response))
        return records

    # ------------------------------------------------------------------ review endpoints

    async def snippet_search(self, query: str, limit: int = 10) -> list[Snippet]:
        """Passage-level search: returns evidence text, not just titles (ADR-005).

        Snippet results carry only a corpus id and minimal metadata, so the papers are
        hydrated in a single follow-up `/paper/batch` call rather than one call each.

        Raises `ProviderEndpointUnavailable` when unauthenticated. Callers gate on
        `search_pool_available` and drop the strategy; reaching here without one is a
        wiring bug, and it fails here rather than four doomed retries later.
        """
        self._require_search_pool("/snippet/search")
        response = await self.graph.get_json(
            "/snippet/search",
            params={"query": query, "limit": min(limit, 100)},
            ttl_s=TTL.SNIPPET,
        )
        rows = response.body.get("data") or []

        corpus_refs: list[str] = []
        staged: list[tuple[dict, dict, float]] = []
        for row in rows:
            snippet = row.get("snippet") or {}
            paper = row.get("paper") or {}
            corpus_id = paper.get("corpusId") or paper.get("corpusid")
            if not corpus_id or not (snippet.get("text") or "").strip():
                continue
            corpus_refs.append(f"CorpusId:{corpus_id}")
            staged.append((snippet, paper, float(row.get("score") or 0.0)))

        if not staged:
            return []

        hydrated = await self.batch_hydrate(corpus_refs)
        by_corpus: dict[str, SourceRecord] = {}
        for record in hydrated:
            corpus = (record.csl.get("custom") or {}).get("s2_corpus_id")
            if corpus:
                by_corpus[str(corpus)] = record

        snippets: list[Snippet] = []
        for snippet, paper, score in staged:
            hydrated_record = by_corpus.get(
                str(paper.get("corpusId") or paper.get("corpusid"))
            )
            if hydrated_record is None:
                # Hydration found nothing for this corpus id. Dropped rather than
                # fabricated: without a stored record there is no source_id to cite, and
                # inventing one is the exact failure HR-1 exists to prevent.
                continue
            snippets.append(
                Snippet(
                    text=snippet["text"].strip(),
                    kind=snippet.get("snippetKind") or "body",
                    section=snippet.get("section"),
                    score=score,
                    source_id=hydrated_record.source_id,
                    record=hydrated_record,
                )
            )
        return snippets

    async def recommendations_from(
        self,
        positive_paper_ids: list[str],
        *,
        limit: int = 20,
        negative_paper_ids: list[str] | None = None,
    ) -> list[SourceRecord]:
        """SPECTER2-space recommendations seeded with the paper's own cited works.

        `positive_paper_ids` are S2-native ids; pass the bibliography's resolved works.
        The API caps the seed set, so we send the most recent seeds — a neighbourhood
        defined by what the authors cited *lately* is the one where a missed paper is
        most likely to be a real omission rather than an older classic.
        """
        seeds = [pid for pid in positive_paper_ids if pid][:100]
        if not seeds:
            return []
        body: dict[str, Any] = {"positivePaperIds": sorted(seeds)}
        if negative_paper_ids:
            body["negativePaperIds"] = sorted({p for p in negative_paper_ids if p})[:100]

        response = await self.recommendations.post_json(
            "/papers",
            params={"fields": S2_RECOMMENDATION_FIELDS, "limit": min(limit, 500)},
            json_body=body,
            ttl_s=TTL.RECOMMENDATIONS,
        )
        papers = response.body.get("recommendedPapers") or response.body.get("data") or []
        return await self._store_papers(papers, response)

    # ------------------------------------------------------------------ internals

    async def _resolve_refs(self, ids: list[str]) -> list[str]:
        """Turn a mixed list of our ids and S2-native refs into S2-native refs."""
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
                ref = _paper_ref(extract_identifiers(record.csl))
                if ref:
                    native.append(ref)
            else:
                native.append(identifier)
        return native

    async def _store_papers(
        self, papers: list[dict], response: ProviderResponse
    ) -> list[SourceRecord]:
        """Write every paper to the append-only store and return the stored records.

        This is the *only* place S2 data becomes a `source_id`, and it happens with
        provenance minted from `response` — the actual HTTP response these papers came
        out of. HR-1.
        """
        records: list[SourceRecord] = []
        for paper in papers:
            record = self._to_record(paper, response)
            if record is None:
                continue
            await self.store.put(record)
            stored = self.store.get(record.source_id)
            records.append(stored or record)
        return records

    def _to_record(self, paper: dict, response: ProviderResponse) -> SourceRecord | None:
        if not paper or not (paper.get("title") or "").strip():
            # A record with no title cannot be keyed, cited, or shown to a reader.
            return None
        csl = s2_paper_to_csl(paper)
        ids = extract_identifiers(csl)
        try:
            source_id = mint_source_id(ids)
            external_url = external_url_for(ids, fallback=paper.get("url"))
        except ValueError:
            # No DOI, no provider id, no usable title. Not storable, and not guessable.
            return None

        abstract = (paper.get("abstract") or "").strip()
        abstract_source = AbstractSource.UNAVAILABLE
        if abstract:
            abstract_source = AbstractSource.S2
        else:
            tldr = ((paper.get("tldr") or {}).get("text") or "").strip()
            if tldr:
                abstract, abstract_source = tldr, AbstractSource.TLDR

        return SourceRecord(
            source_id=source_id,
            csl=csl,
            provenance=response.provenance(external_url),
            abstract=abstract or None,
            abstract_source=abstract_source,
        )

    async def _enrich_abstract(
        self,
        record: SourceRecord,
        response: ProviderResponse,
        abstract: str,
        source: AbstractSource,
    ) -> None:
        """Append the abstract to an existing record as a new version."""
        ids = extract_identifiers(record.csl)
        await self.store.put(
            SourceRecord(
                source_id=record.source_id,
                csl=record.csl,
                provenance=response.provenance(
                    external_url_for(ids, fallback=record.provenance.external_url)
                ),
                abstract=abstract,
                abstract_source=source,
            )
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "graph": self.graph.snapshot(),
            "recommendations": self.recommendations.snapshot(),
        }


def _paper_ref(ids: WorkIdentifiers) -> str | None:
    """The most reliable S2-native reference for a work.

    `paperId` first because it needs no resolution on S2's side; DOI next because it
    resolves for anything published; corpus id last because it is S2-internal and only
    appears in snippet results.
    """
    if ids.s2_paper_id:
        return ids.s2_paper_id
    doi = normalize_doi(ids.doi)
    if doi:
        return f"DOI:{doi}"
    if ids.s2_corpus_id:
        return f"CorpusId:{ids.s2_corpus_id}"
    if ids.arxiv_id:
        return f"ARXIV:{ids.arxiv_id}"
    if ids.pmid:
        return f"PMID:{ids.pmid}"
    return None
