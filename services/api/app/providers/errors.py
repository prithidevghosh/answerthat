"""Provider-layer errors.

Appendix A names `MissingAPIKeyError` and `ProviderRateLimited`; `app/core/errors.py`
adds `SourceStoreViolation`. This module adds only what the provider layer itself needs
and re-exports the rest so an adapter has one import site.

Every class here exists so a failure can be *named and raised* (HR-3). None of them has
a "return empty instead" counterpart, and none should acquire one. In particular:

* `ProviderRateLimited` and `ProviderBudgetExhausted` are raised, never converted into
  an empty result list. A throttled search and a genuinely empty search are
  indistinguishable to everything downstream, and the review pipeline would report the
  first as "no missing work found" (ADR-010).
* `SourceStoreViolation` subclasses mean HR-1 was nearly broken. The fix is always to
  route the write through an adapter backed by a real HTTP response, never to relax the
  check.
"""

from __future__ import annotations

from app.core.contracts import MissingAPIKeyError, ProviderRateLimited
from app.core.errors import SourceStoreViolation

__all__ = [
    "MissingAPIKeyError",
    "ProviderRateLimited",
    "SourceStoreViolation",
    "ProviderHTTPError",
    "ProviderUnavailable",
    "ProviderEndpointUnavailable",
    "ProviderBudgetExhausted",
    "AppendOnlyViolation",
    "UnprovenanceredSource",
    "CacheUnavailable",
]


class ProviderHTTPError(RuntimeError):
    """A provider answered with a non-success status we will not retry.

    Carries the status and a truncated body: an upstream failure the operator cannot
    diagnose is barely better than a silent one.
    """

    def __init__(self, provider: str, endpoint: str, status_code: int, body: str = "") -> None:
        self.provider = provider
        self.endpoint = endpoint
        self.status_code = status_code
        self.body = body[:500]
        super().__init__(
            f"{provider} {endpoint} returned HTTP {status_code}"
            + (f": {self.body}" if self.body else "")
        )


class ProviderUnavailable(RuntimeError):
    """Transport failed (DNS, connect, read timeout) and the retry budget is spent.

    Distinct from `ProviderHTTPError`: we never got an answer, rather than got a bad one.
    """

    def __init__(self, provider: str, endpoint: str, attempts: int, cause: str) -> None:
        self.provider = provider
        self.endpoint = endpoint
        self.attempts = attempts
        super().__init__(
            f"{provider} {endpoint} unreachable after {attempts} attempt(s): {cause}"
        )


class ProviderEndpointUnavailable(RuntimeError):
    """An endpoint this regime cannot use was called anyway.

    Deliberately **not** a `ProviderRateLimited` subclass. That error means "we asked and
    were throttled"; this one means "we did not ask, because in this configuration the
    endpoint has no working answer to give". Conflating them would let a caller that
    forgot to check a capability look, in the logs, exactly like one that tried honestly
    and lost a race for the shared pool.

    Raised rather than returning `[]`, for the ADR-010 reason that governs everything in
    this module: the caller's job is to *drop the strategy and say so*, not to receive an
    empty list it cannot distinguish from a thin literature.
    """

    def __init__(self, provider: str, endpoint: str, reason: str) -> None:
        self.provider = provider
        self.endpoint = endpoint
        self.reason = reason
        super().__init__(f"{provider} {endpoint} is unavailable in this regime: {reason}")


class ProviderBudgetExhausted(ProviderRateLimited):
    """A metered provider's budget for the period is spent.

    Subclasses `ProviderRateLimited` so callers that already handle throttling handle
    this too. OpenAlex is credit-metered, not request-metered: a list query costs 10
    credits and a vector query 1000, so "we are out of requests" is the wrong mental
    model and the wrong error.
    """

    def __init__(self, provider: str, requested: int, remaining: int, period: str) -> None:
        self.provider = provider
        self.requested = requested
        self.remaining = remaining
        super().__init__(
            f"{provider} budget exhausted for the current {period}: "
            f"needed {requested} credit(s), {remaining} remaining. "
            "Raising rather than returning an empty result — an unfunded search and an "
            "empty literature are indistinguishable downstream (ADR-010)."
        )


class AppendOnlyViolation(SourceStoreViolation):
    """A `put()` would have overwritten or contradicted an existing source record.

    The store accepts strict enrichment of an existing `source_id` (an abstract arriving
    later through the fallback chain) as a new version. It never accepts a field
    changing value or reverting to null. HR-1.
    """


class UnprovenanceredSource(SourceStoreViolation):
    """A `put()` carried a `Provenance` that no HTTP response minted.

    The store only accepts provenance created by `ProviderHTTP` from a real response, so
    a hand-built or model-built `SourceRecord` cannot enter the store even from inside
    `app/providers/`. HR-1.
    """


class CacheUnavailable(RuntimeError):
    """The response cache could not be read or written.

    Raised rather than skipped. A cache that silently stops working turns a reproducible
    demo into a live-fire rate-limit test, and the failure would only surface as
    mysterious throttling hours later.
    """
