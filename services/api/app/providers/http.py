"""The single HTTP path every provider adapter goes through.

It does five things that must not be done per-adapter:

1. **Rate limiting and credit budgeting** before the request leaves.
2. **Caching**, keyed by semantic parameters only — credentials are injected here, after
   the cache key is derived, so they never enter it.
3. **Bounded retries** on 429 and 5xx, honouring `Retry-After`, then *raising*.
4. **Credential injection**, so no adapter is in a position to forget it.
5. **Minting `Provenance`** — and this one is load-bearing for HR-1.

On (5): a `Provenance` can only be created by `ProviderResponse.provenance()`, which
exists only on an object this module returns after a real response (live or replayed from
a cache entry that was itself a real response). Each one is registered in
`PROVENANCE_REGISTRY`, and `source_store.put()` refuses any record whose provenance is not
in it. So a `SourceRecord` assembled by hand — or by a model — cannot enter the store even
from inside `app/providers/`. The comment in Appendix A saying "proof this came from an
HTTP response" is enforced here, mechanically, rather than trusted.

Nothing in this module converts a failure into an empty result. HR-3.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from app.core.contracts import Provenance, ProviderRateLimited
from app.providers.cache import ResponseCache, normalize_query
from app.providers.errors import ProviderHTTPError, ProviderUnavailable
from app.providers.ratelimit import CreditBudget, TokenBucket

__all__ = [
    "ProviderHTTP",
    "ProviderResponse",
    "ProvenanceRegistry",
    "PROVENANCE_REGISTRY",
    "ProviderName",
]

ProviderName = Literal["semantic_scholar", "openalex", "crossref"]

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- provenance registry


def provenance_fingerprint(p: Provenance) -> str:
    raw = f"{p.provider}\x00{p.endpoint}\x00{p.retrieved_at}\x00{p.external_url}".encode()
    return hashlib.sha256(raw).hexdigest()


class ProvenanceRegistry:
    """Fingerprints of every `Provenance` minted from a real provider response.

    Bounded, insertion-ordered. Adapters mint provenance and write the record within the
    same call, so the window is milliseconds; the bound exists to stop a long-running
    worker growing without limit, not to expire anything meaningful.

    Eviction is a *refusal*, never a pass: if a fingerprint is gone, `source_store.put()`
    rejects the record rather than assuming it was fine.
    """

    def __init__(self, maxsize: int = 100_000) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self.maxsize = maxsize
        self.minted = 0

    def register(self, provenance: Provenance) -> None:
        fp = provenance_fingerprint(provenance)
        self._seen[fp] = None
        self._seen.move_to_end(fp)
        self.minted += 1
        while len(self._seen) > self.maxsize:
            self._seen.popitem(last=False)

    def contains(self, provenance: Provenance) -> bool:
        return provenance_fingerprint(provenance) in self._seen

    def __len__(self) -> int:
        return len(self._seen)


#: Process-wide. `source_store` consults exactly this instance.
PROVENANCE_REGISTRY = ProvenanceRegistry()


# --------------------------------------------------------------------------- response


@dataclass(frozen=True)
class ProviderResponse:
    """A real provider response, live or replayed from cache.

    `retrieved_at` is the moment the *original* HTTP call happened, preserved across
    cache hits: a cached record's provenance should say when the data was actually
    fetched, not when we last read our own cache.
    """

    provider: ProviderName
    endpoint: str
    url: str
    body: dict
    retrieved_at: str
    from_cache: bool

    def provenance(self, external_url: str) -> Provenance:
        """Mint provenance for a record derived from this response.

        `external_url` must be the resolvable public URL of the *record* (its DOI, S2 or
        OpenAlex landing page) — not our request URL. It is what a reader clicks to check
        that we did not invent the source, so it is validated in `source_store.put()`.
        """
        p = Provenance(
            provider=self.provider,
            endpoint=self.endpoint,
            retrieved_at=self.retrieved_at,
            external_url=external_url,
        )
        PROVENANCE_REGISTRY.register(p)
        return p


# --------------------------------------------------------------------------- client


@dataclass
class RetryPolicy:
    max_attempts: int = 4
    base_backoff_s: float = 1.0
    max_backoff_s: float = 30.0
    # A provider's own Retry-After is authoritative, but we cap it: an hour-long hint
    # should surface as an error the operator sees, not as a silently stalled review.
    max_retry_after_s: float = 60.0


class ProviderHTTP:
    """Per-provider HTTP client. One instance per adapter."""

    def __init__(
        self,
        *,
        provider: ProviderName,
        base_url: str,
        cache: ResponseCache,
        limiter: TokenBucket,
        auth_headers: dict[str, str] | None = None,
        auth_params: dict[str, str] | None = None,
        budget: CreditBudget | None = None,
        user_agent: str,
        timeout_s: float = 30.0,
        retry: RetryPolicy | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.cache = cache
        self.limiter = limiter
        self.budget = budget
        self._auth_headers = dict(auth_headers or {})
        self._auth_params = dict(auth_params or {})
        self.retry = retry or RetryPolicy()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )
        self.live_calls = 0
        self.cache_hits = 0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---------------- public API ----------------

    async def get_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        ttl_s: int,
        credits: int = 0,
    ) -> ProviderResponse:
        return await self._request("GET", endpoint, params=params, json_body=None, ttl_s=ttl_s, credits=credits)

    async def post_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        ttl_s: int,
        credits: int = 0,
    ) -> ProviderResponse:
        return await self._request("POST", endpoint, params=params, json_body=json_body, ttl_s=ttl_s, credits=credits)

    # ---------------- internals ----------------

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        ttl_s: int,
        credits: int,
    ) -> ProviderResponse:
        # The cache key covers method, semantic params and body — never credentials.
        canonical, query_hash = normalize_query(
            {"method": method, "params": params or {}, "body": json_body or {}}
        )

        hit = await self.cache.get(self.provider, endpoint, query_hash)
        if hit is not None:
            self.cache_hits += 1
            env = hit.payload
            return ProviderResponse(
                provider=self.provider,
                endpoint=endpoint,
                url=env.get("url", ""),
                body=env.get("body", {}),
                # Preserved from the original call, not the cache read.
                retrieved_at=env.get("retrieved_at", hit.stored_at.isoformat()),
                from_cache=True,
            )

        body, final_url = await self._send_with_retries(
            method, endpoint, params, json_body, credits=credits
        )

        retrieved_at = _utcnow_iso()
        await self.cache.put(
            self.provider,
            endpoint,
            query_hash,
            canonical,
            {"url": final_url, "body": body, "retrieved_at": retrieved_at},
            ttl_s,
        )
        return ProviderResponse(
            provider=self.provider,
            endpoint=endpoint,
            url=final_url,
            body=body,
            retrieved_at=retrieved_at,
            from_cache=False,
        )

    async def _send_with_retries(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        credits: int = 0,
    ) -> tuple[dict, str]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        send_params: dict[str, Any] = {k: v for k, v in (params or {}).items() if v is not None}
        send_params.update(self._auth_params)

        last_error = ""
        for attempt in range(1, self.retry.max_attempts + 1):
            # Charged per attempt, not per call: a retried request is metered again by
            # the provider, and a budget that undercounts retries is a budget that runs
            # out without warning. Never refunded — a failure after the request left the
            # process may well have been billed upstream, so we assume it was.
            if credits and self.budget is not None:
                self.budget.charge(credits, endpoint=endpoint)
            await self.limiter.acquire()
            self.live_calls += 1
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=send_params,
                    json=json_body,
                    headers=self._auth_headers or None,
                )
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt == self.retry.max_attempts:
                    raise ProviderUnavailable(self.provider, endpoint, attempt, last_error) from exc
                await asyncio.sleep(self._backoff(attempt))
                continue

            if response.status_code == 401 or response.status_code == 403:
                # Not retried. With HR-2 in force these mean the key is wrong or revoked,
                # and retrying an invalid credential just burns the rate limit.
                raise ProviderHTTPError(
                    self.provider, endpoint, response.status_code, _safe_text(response)
                )

            if response.status_code in _RETRYABLE_STATUS:
                retry_after = _retry_after_seconds(response, self.retry.max_retry_after_s)
                if response.status_code == 429:
                    await self.limiter.penalise(retry_after or self._backoff(attempt))
                if attempt == self.retry.max_attempts:
                    if response.status_code == 429:
                        raise ProviderRateLimited(
                            f"{self.provider} {endpoint} throttled (HTTP 429) after "
                            f"{attempt} attempts. Raising rather than returning an empty "
                            "result — a throttled search and an empty literature are "
                            "indistinguishable downstream (ADR-010)."
                        )
                    raise ProviderHTTPError(
                        self.provider, endpoint, response.status_code, _safe_text(response)
                    )
                await asyncio.sleep(retry_after or self._backoff(attempt))
                continue

            if response.status_code >= 400:
                raise ProviderHTTPError(
                    self.provider, endpoint, response.status_code, _safe_text(response)
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderHTTPError(
                    self.provider,
                    endpoint,
                    response.status_code,
                    f"response was not JSON: {_safe_text(response)}",
                ) from exc

            # Some endpoints answer with a bare list. Envelope it so the cache and the
            # rest of the code only ever handle one shape.
            body = payload if isinstance(payload, dict) else {"data": payload}
            return body, str(response.url)

        # Unreachable: every branch above either returns or raises on the last attempt.
        raise ProviderUnavailable(self.provider, endpoint, self.retry.max_attempts, last_error)

    def _backoff(self, attempt: int) -> float:
        raw = min(self.retry.max_backoff_s, self.retry.base_backoff_s * (2 ** (attempt - 1)))
        # Jitter, so parallel per-claim strategies do not re-collide in lockstep.
        return raw * (0.5 + random.random() / 2)

    def snapshot(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "live_calls": self.live_calls,
            "cache_hits": self.cache_hits,
            "limiter": self.limiter.snapshot(),
            "budget": self.budget.snapshot() if self.budget else None,
        }


def _safe_text(response: httpx.Response) -> str:
    try:
        return response.text[:500]
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        return f"<{len(response.content)} bytes, undecodable>"


def _retry_after_seconds(response: httpx.Response, cap: float) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(cap, max(0.0, float(raw.strip())))
    except ValueError:
        # HTTP-date form. Rare from these providers; fall back to our own backoff.
        return None


def is_resolvable_url(url: str) -> bool:
    """Structural check that a URL could resolve: absolute, http(s), with a host.

    We cannot prove a URL resolves without fetching it, and fetching every candidate's
    landing page would double our request volume. This rules out the failure that
    actually matters — an `external_url` that is empty, relative, or a made-up scheme.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


@dataclass
class ProviderStats:
    """Aggregated call accounting, surfaced with review progress."""

    live_calls: int = 0
    cache_hits: int = 0
    per_provider: dict[str, dict[str, Any]] = field(default_factory=dict)
