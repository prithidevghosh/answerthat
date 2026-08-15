"""Token-bucket rate limiting and credit budgeting, per provider.

Two different meters, because the two providers meter differently:

* **Semantic Scholar** is *request*-metered at roughly 1 request/second with a key.
  Everything batches or queues. `TokenBucket` is the queue.
* **OpenAlex** is *credit*-metered, not request-metered: 1 credit for a singleton, 10 for
  a list query, 100 for content, 1000 for vector search, against a free-key allowance of
  100k credits/day. Counting requests there would let a handful of vector calls quietly
  burn the day's allowance. `CreditBudget` is the meter that matters.

Both raise on exhaustion. Neither returns an empty result, and neither grows a
`return []` branch. HR-3, and the specific reasoning of ADR-010: a throttled empty search
and a genuinely empty literature are indistinguishable to the review pipeline, so an
exhausted limiter that yields nothing would surface to the researcher as "no missing work
found".
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum

from app.core.contracts import ProviderRateLimited
from app.providers.errors import ProviderBudgetExhausted

__all__ = [
    "TokenBucket",
    "CreditBudget",
    "OpenAlexCost",
    "S2_REQUESTS_PER_SECOND",
    "OPENALEX_FREE_DAILY_CREDITS",
]

# Semantic Scholar's authenticated allowance. Documented as ~1 rps; we run marginally
# under it because bursting into a 429 costs more wall-clock than the token we saved.
S2_REQUESTS_PER_SECOND = 0.9

# A free OpenAlex key's daily allowance.
OPENALEX_FREE_DAILY_CREDITS = 100_000


class OpenAlexCost(IntEnum):
    """Credit cost per OpenAlex endpoint class. See memory.md §3.

    VECTOR is 100x a list query. It stays off by default and behind an explicit budget
    gate — a loop that reaches for it is a day's allowance in 100 calls.
    """

    SINGLETON = 1
    LIST = 10
    CONTENT = 100
    VECTOR = 1000


class TokenBucket:
    """Async token bucket with fair FIFO queueing.

    Callers `await acquire()`; the bucket sleeps them until a token is available. The
    lock is deliberately held across the sleep so that waiters are served in arrival
    order — without that, a burst of coroutines against a 1 rps provider produces
    unbounded, unfair latency for whichever coroutine loses the race each time.

    `max_wait_s` bounds how long a single acquisition may block. Exceeding it raises
    `ProviderRateLimited`. Review is a minutes-long streaming job by design (ADR-014), so
    the default is generous; it exists to catch a genuinely stuck pipeline, not to make
    slow calls fail.
    """

    def __init__(
        self,
        rate_per_sec: float,
        *,
        capacity: float | None = None,
        name: str = "provider",
        max_wait_s: float = 120.0,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self.name = name
        self.rate = rate_per_sec
        # A burst of one by default: at ~1 rps there is nothing to burst with, and a
        # larger bucket only front-loads the 429 we are trying to avoid.
        self.capacity = capacity if capacity is not None else max(1.0, rate_per_sec)
        self.max_wait_s = max_wait_s
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()
        self.total_acquired = 0
        self.total_wait_s = 0.0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._updated = now

    async def acquire(self, tokens: float = 1.0) -> None:
        """Block until `tokens` are available. Raises `ProviderRateLimited` past the budget."""
        if tokens > self.capacity:
            raise ValueError(
                f"{self.name}: cannot acquire {tokens} tokens from a bucket of capacity "
                f"{self.capacity}"
            )
        started = time.monotonic()
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    waited = time.monotonic() - started
                    self.total_acquired += 1
                    self.total_wait_s += waited
                    return
                deficit = tokens - self._tokens
                sleep_for = deficit / self.rate
                if (time.monotonic() - started) + sleep_for > self.max_wait_s:
                    raise ProviderRateLimited(
                        f"{self.name}: rate limit wait would exceed {self.max_wait_s:.0f}s "
                        f"(need {deficit:.2f} more token(s) at {self.rate:.2f}/s). "
                        "Raising rather than proceeding unthrottled or returning empty."
                    )
                await asyncio.sleep(sleep_for)

    async def penalise(self, seconds: float) -> None:
        """Drain the bucket for `seconds` after an upstream 429 / Retry-After.

        Applied when the provider tells us we are over its limit — our model of its
        budget was wrong, so we back off on its terms rather than ours.
        """
        async with self._lock:
            self._refill()
            self._tokens = min(self._tokens, 0.0) - max(0.0, seconds) * self.rate

    def snapshot(self) -> dict[str, float | int | str]:
        return {
            "name": self.name,
            "rate_per_sec": self.rate,
            "capacity": self.capacity,
            "acquisitions": self.total_acquired,
            "total_wait_s": round(self.total_wait_s, 3),
        }


def _next_utc_midnight(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass
class CreditBudget:
    """Daily credit accounting for a metered provider.

    Deliberately fails closed: a charge that would cross the limit raises
    `ProviderBudgetExhausted` *before* the request goes out, so we never spend credits on
    a call whose result we then have to discard.

    In-process, resetting at UTC midnight. That is the right scope for a single API
    worker; if we ever run several, this becomes a Redis counter and the call sites do
    not change.
    """

    daily_limit: int = OPENALEX_FREE_DAILY_CREDITS
    name: str = "openalex"
    # Below this many remaining credits, only cheap endpoints are allowed through. Keeps
    # a reserve for the singleton lookups that abstract hydration depends on.
    reserve: int = 2_000
    used: int = field(default=0, init=False)
    _period_end: datetime = field(default_factory=lambda: _next_utc_midnight(_utcnow()), init=False)

    def _roll_period(self) -> None:
        now = _utcnow()
        if now >= self._period_end:
            self.used = 0
            self._period_end = _next_utc_midnight(now)

    @property
    def remaining(self) -> int:
        self._roll_period()
        return max(0, self.daily_limit - self.used)

    def charge(self, credits: int, *, endpoint: str = "") -> None:
        """Reserve `credits` or raise. Call before issuing the request."""
        self._roll_period()
        if credits <= 0:
            raise ValueError("credits must be positive")
        if self.used + credits > self.daily_limit:
            raise ProviderBudgetExhausted(
                self.name, credits, self.daily_limit - self.used, "day"
            )
        self.used += credits

    def can_afford(self, credits: int, *, respect_reserve: bool = True) -> bool:
        """Whether a charge would succeed. Used to gate optional, expensive endpoints."""
        floor = self.reserve if respect_reserve else 0
        return self.remaining - credits >= floor

    def refund(self, credits: int) -> None:
        """Return credits reserved for a call that never reached the provider.

        Only for local failures before the request is sent (a cache race, a cancelled
        task). A request that reached OpenAlex was charged by OpenAlex too.
        """
        self._roll_period()
        self.used = max(0, self.used - credits)

    def snapshot(self) -> dict[str, int | str]:
        self._roll_period()
        return {
            "name": self.name,
            "daily_limit": self.daily_limit,
            "used": self.used,
            "remaining": self.remaining,
            "period_ends": self._period_end.isoformat(),
        }


def _utcnow() -> datetime:
    return datetime.now(UTC)
