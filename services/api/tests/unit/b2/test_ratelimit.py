"""Token bucket and credit budget. HR-3: exhaustion raises, it never yields empty."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.contracts import ProviderRateLimited
from app.providers.errors import ProviderBudgetExhausted
from app.providers.ratelimit import CreditBudget, OpenAlexCost, TokenBucket


async def test_bucket_paces_calls_at_the_configured_rate() -> None:
    bucket = TokenBucket(20.0, capacity=1.0, name="test")
    started = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - started
    # First acquisition is free (the bucket starts full), the other four cost 1/20s each.
    assert elapsed >= 4 / 20 * 0.8
    assert bucket.total_acquired == 5


async def test_bucket_serves_waiters_in_arrival_order() -> None:
    """FIFO matters at ~1 rps: unfair queueing starves whichever claim loses each race."""
    bucket = TokenBucket(50.0, capacity=1.0, name="test")
    order: list[int] = []

    async def worker(n: int) -> None:
        await bucket.acquire()
        order.append(n)

    await asyncio.gather(*(worker(i) for i in range(6)))
    assert order == list(range(6))


async def test_bucket_raises_rather_than_waiting_forever() -> None:
    bucket = TokenBucket(1.0, capacity=1.0, name="s2", max_wait_s=0.05)
    await bucket.acquire()
    with pytest.raises(ProviderRateLimited) as exc:
        await bucket.acquire()
    assert "Raising rather than proceeding unthrottled or returning empty" in str(exc.value)


async def test_penalise_backs_off_on_the_providers_terms() -> None:
    bucket = TokenBucket(100.0, capacity=1.0, name="s2", max_wait_s=0.01)
    await bucket.penalise(5.0)
    with pytest.raises(ProviderRateLimited):
        await bucket.acquire()


def test_credit_budget_charges_by_endpoint_class_not_by_request() -> None:
    budget = CreditBudget(daily_limit=100, name="openalex", reserve=0)
    budget.charge(OpenAlexCost.LIST)
    budget.charge(OpenAlexCost.SINGLETON)
    assert budget.used == 11
    assert budget.remaining == 89


def test_credit_budget_raises_before_the_request_goes_out() -> None:
    budget = CreditBudget(daily_limit=15, name="openalex", reserve=0)
    budget.charge(OpenAlexCost.LIST)
    with pytest.raises(ProviderBudgetExhausted) as exc:
        budget.charge(OpenAlexCost.LIST)
    assert budget.used == 10, "a refused charge must not be recorded"
    assert "indistinguishable downstream" in str(exc.value)


def test_budget_exhaustion_is_a_rate_limit_to_callers() -> None:
    """Callers that already handle throttling must handle credit exhaustion too."""
    assert issubclass(ProviderBudgetExhausted, ProviderRateLimited)


def test_vector_endpoint_is_gated_by_the_reserve() -> None:
    """VECTOR is 100x a list query; the reserve keeps hydration alive when it is close."""
    budget = CreditBudget(daily_limit=100_000, name="openalex", reserve=2_000)
    budget.charge(97_500)
    assert budget.can_afford(OpenAlexCost.SINGLETON) is True
    assert budget.can_afford(OpenAlexCost.VECTOR) is False
    assert budget.can_afford(OpenAlexCost.VECTOR, respect_reserve=False) is True
