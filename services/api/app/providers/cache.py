"""Provider response cache, keyed by `(provider, endpoint, normalized_query_hash)`.

Built before the adapters, not after, because it changes what the adapters can do. With
it, re-reviewing a paper is nearly free and a demo is reproducible; without it, every run
is a live-fire rate-limit test against a 1 rps provider.

Two rules the key derivation exists to enforce:

* **Credentials never enter a cache key.** The HTTP layer passes semantic parameters
  here and injects `api_key` / `mailto` at send time. A key that varied by credential
  would leak it into the canonical query column and halve the hit rate for nothing.
* **Normalization is stable.** Unicode NFKC, casefold, collapsed whitespace, sorted
  containers. `"Attention  Is All You Need"` and `"attention is all you need"` are one
  cache entry. Adapters must therefore also canonicalize the *request* the same way —
  see `SemanticScholarProvider.batch_hydrate`, which sorts ids before sending and maps
  results back by id rather than by position.

Cache failures raise `CacheUnavailable`. They are not skipped: a cache that silently
stops working looks exactly like unexplained throttling several hours later.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import DateTime, Index, String, Text, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import JSONB, Base, session_scope, utcnow
from app.providers.errors import CacheUnavailable

__all__ = [
    "ResponseCache",
    "PostgresResponseCache",
    "InMemoryResponseCache",
    "ProviderCacheRow",
    "normalize_query",
    "cache_key",
    "TTL",
]


class TTL:
    """Default lifetimes, in seconds, by kind of call.

    Bibliographic records barely change; relevance rankings change slowly; nothing here
    is time-sensitive enough to warrant a short TTL, and a short TTL is paid for in
    rate-limited seconds.
    """

    SEARCH = int(timedelta(days=7).total_seconds())
    MATCH = int(timedelta(days=30).total_seconds())
    RECORD = int(timedelta(days=30).total_seconds())
    SNIPPET = int(timedelta(days=7).total_seconds())
    RECOMMENDATIONS = int(timedelta(days=7).total_seconds())
    GRAPH = int(timedelta(days=7).total_seconds())


# --------------------------------------------------------------------------- key derivation


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        folded = unicodedata.normalize("NFKC", value).casefold()
        return " ".join(folded.split())
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return _normalize_scalar(str(value))


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        normalized = [_normalize(v) for v in value]
        # Sorted so that request order cannot fragment the cache. Adapters that care
        # about ordering must sort their request too and map the response back by id.
        return sorted(normalized, key=lambda v: json.dumps(v, sort_keys=True, default=str))
    return _normalize_scalar(value)


def normalize_query(params: Mapping[str, Any] | None) -> tuple[str, str]:
    """Return `(canonical_json, sha256_hex)` for a set of semantic request parameters.

    Never pass credentials in here. `None`-valued parameters are dropped, so an
    explicitly-unset optional and an omitted one share a cache entry.
    """
    cleaned = {k: v for k, v in (params or {}).items() if v is not None}
    canonical = json.dumps(_normalize(cleaned), sort_keys=True, separators=(",", ":"), default=str)
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_key(provider: str, endpoint: str, query_hash: str) -> str:
    raw = f"{provider}\x00{endpoint}\x00{query_hash}".encode()
    return hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------- table


class ProviderCacheRow(Base):
    """One cached provider response.

    Not append-only — unlike `source_store`, this is a cache and an expired entry is
    meant to be replaced. Nothing downstream holds a foreign key into it.
    """

    __tablename__ = "provider_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Kept readable on purpose: when a cache hit returns something surprising, the first
    # question is always "what exactly did we ask for?"
    canonical_query: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    stored_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_provider_cache_lookup", "provider", "endpoint", "query_hash"),
        Index("ix_provider_cache_expires_at", "expires_at"),
    )


# --------------------------------------------------------------------------- interface


@dataclass(frozen=True)
class CacheHit:
    payload: dict
    stored_at: Any


@runtime_checkable
class ResponseCache(Protocol):
    """Every provider call goes through one of these.

    It is a required constructor argument on every adapter, with no default. A cache that
    could be omitted would be omitted, and the first symptom would be a rate limit in the
    middle of a demo.
    """

    async def get(self, provider: str, endpoint: str, query_hash: str) -> CacheHit | None: ...

    async def put(
        self,
        provider: str,
        endpoint: str,
        query_hash: str,
        canonical_query: str,
        payload: dict,
        ttl_s: int,
    ) -> None: ...


class PostgresResponseCache:
    """The production cache. Survives restarts, which is the whole point."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    async def get(self, provider: str, endpoint: str, query_hash: str) -> CacheHit | None:
        if not self.enabled:
            return None
        key = cache_key(provider, endpoint, query_hash)
        try:
            async with session_scope() as session:
                row = await session.get(ProviderCacheRow, key)
                if row is None:
                    self.misses += 1
                    return None
                if row.expires_at <= utcnow():
                    # Expired entries are removed on read rather than by a sweeper: the
                    # read path is the only place we reliably know an entry is stale.
                    await session.execute(
                        delete(ProviderCacheRow).where(ProviderCacheRow.cache_key == key)
                    )
                    self.misses += 1
                    return None
                self.hits += 1
                return CacheHit(payload=row.payload, stored_at=row.stored_at)
        except SQLAlchemyError as exc:
            raise CacheUnavailable(f"cache read failed for {provider} {endpoint}: {exc}") from exc

    async def put(
        self,
        provider: str,
        endpoint: str,
        query_hash: str,
        canonical_query: str,
        payload: dict,
        ttl_s: int,
    ) -> None:
        if not self.enabled:
            return
        key = cache_key(provider, endpoint, query_hash)
        now = utcnow()
        values = {
            "cache_key": key,
            "provider": provider,
            "endpoint": endpoint,
            "query_hash": query_hash,
            "canonical_query": canonical_query,
            "payload": payload,
            "stored_at": now,
            "expires_at": now + timedelta(seconds=ttl_s),
        }
        try:
            async with session_scope() as session:
                stmt = pg_insert(ProviderCacheRow).values(**values)
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[ProviderCacheRow.cache_key],
                        set_={
                            "payload": stmt.excluded.payload,
                            "stored_at": stmt.excluded.stored_at,
                            "expires_at": stmt.excluded.expires_at,
                        },
                    )
                )
        except SQLAlchemyError as exc:
            raise CacheUnavailable(f"cache write failed for {provider} {endpoint}: {exc}") from exc

    async def purge_expired(self) -> int:
        try:
            async with session_scope() as session:
                result = await session.execute(
                    delete(ProviderCacheRow).where(ProviderCacheRow.expires_at <= utcnow())
                )
                return int(getattr(result, "rowcount", 0) or 0)
        except SQLAlchemyError as exc:
            raise CacheUnavailable(f"cache purge failed: {exc}") from exc

    async def count(self) -> int:
        async with session_scope() as session:
            rows = await session.execute(select(ProviderCacheRow.cache_key))
            return len(rows.scalars().all())


class InMemoryResponseCache:
    """Process-local cache for unit tests and recorded-fixture runs.

    Deliberately *not* a fallback. Nothing constructs it automatically; production wiring
    names `PostgresResponseCache` explicitly. If this ever becomes the default, a
    restart silently re-opens the rate-limit floodgates.
    """

    def __init__(self) -> None:
        self._rows: dict[str, tuple[dict, Any, Any]] = {}
        self.hits = 0
        self.misses = 0

    async def get(self, provider: str, endpoint: str, query_hash: str) -> CacheHit | None:
        key = cache_key(provider, endpoint, query_hash)
        row = self._rows.get(key)
        if row is None:
            self.misses += 1
            return None
        payload, stored_at, expires_at = row
        if expires_at <= utcnow():
            del self._rows[key]
            self.misses += 1
            return None
        self.hits += 1
        return CacheHit(payload=payload, stored_at=stored_at)

    async def put(
        self,
        provider: str,
        endpoint: str,
        query_hash: str,
        canonical_query: str,
        payload: dict,
        ttl_s: int,
    ) -> None:
        now = utcnow()
        self._rows[cache_key(provider, endpoint, query_hash)] = (
            payload,
            now,
            now + timedelta(seconds=ttl_s),
        )

    def __len__(self) -> int:
        return len(self._rows)
