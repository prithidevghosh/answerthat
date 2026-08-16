"""The abstract fallback chain. Mandatory, ordered, and terminating in a real outcome.

    S2 abstract → OpenAlex inverted index → S2 TLDR → unavailable

The order is not arbitrary. A publisher-licensed S2 abstract is the full text and the best
evidence. OpenAlex's inverted index reconstructs a full abstract for many of the records S2
has no licence for, which is a meaningful fraction. The TLDR is a *generated* one-sentence
summary — real retrieved text, but far thinner, so it is the last resort before giving up.

`unavailable` is the fourth outcome, not the absence of one. ADR-006 makes it the only
legal verifier output when no abstract survives the chain, and it is **displayed**. The
temptation this module exists to remove is a fifth step where we verify against the title
instead: that produces a confident verdict from no evidence, which is worse than saying
we could not check.

Nothing here catches an exception to move to the next step. Rate limits and transport
failures propagate (HR-3) — a throttled S2 must not silently become "no abstract".

Skipping a step is a different act from catching one, and the chain does the first. When
S2 is unauthenticated its `/paper/{id}` endpoint is closed rather than slow, so steps 1
and 3 are not run at all and OpenAlex becomes the first step. The distinction is the
whole point: catching would turn "we were throttled" into "there is no abstract", while
skipping asks a step that can answer and records in `skipped` that the others never ran.
Before this, a 429 at step 1 propagated past step 2 and killed the review — losing the
step that would have succeeded, to protect an honesty property that was never at risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.contracts import AbstractSource, SourceRecord

__all__ = ["AbstractResolver", "AbstractResult", "AbstractCapableProvider"]


class AbstractCapableProvider(Protocol):
    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]: ...


class GatedAbstractProvider(AbstractCapableProvider, Protocol):
    """An abstract provider that can declare a step of the chain unusable up front.

    Only S2 needs this. Read strictly, with no `getattr` default: a collaborator that
    cannot say whether its endpoint works is a wiring bug, and this codebase's recurring
    failure is exactly the one a default would reintroduce — a missing injection point
    that nothing complains about.
    """

    @property
    def search_pool_available(self) -> bool: ...


@dataclass(frozen=True)
class AbstractResult:
    """What the chain found, and where it stopped.

    `attempted` is kept so the UI can say *which* steps were tried rather than only that
    the result was unavailable — HR-3's "the system says it doesn't know" reads better
    with the shape of the not-knowing attached.

    `skipped` is the same argument for steps that never ran. A chain that is short
    because S2 is unauthenticated and one that is short because S2 had nothing produce
    the same `unavailable`, and only the first is a configuration the operator can fix.
    A step appears in exactly one of the two tuples.
    """

    text: str | None
    source: AbstractSource
    attempted: tuple[AbstractSource, ...]
    skipped: tuple[AbstractSource, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.text) and self.source is not AbstractSource.UNAVAILABLE


class AbstractResolver:
    """Runs the chain for a stored record.

    Both providers are required. A resolver built with only one would silently shorten
    the chain, and a shortened chain reports `unavailable` for records the missing step
    would have covered — a false "we could not check" that looks identical to a true one.
    """

    def __init__(
        self,
        *,
        semantic_scholar: GatedAbstractProvider,
        openalex: AbstractCapableProvider,
    ) -> None:
        if semantic_scholar is None or openalex is None:
            raise ValueError(
                "AbstractResolver requires both providers. A partial chain reports "
                "`unavailable` for records the missing step would have resolved, and "
                "that false negative is indistinguishable from a true one (ADR-006)."
            )
        self.s2 = semantic_scholar
        self.openalex = openalex
        # Counters, surfaced with review progress so the proportion of unverifiable
        # findings is visible rather than inferred.
        self.counts: dict[str, int] = {source.value: 0 for source in AbstractSource}

    async def resolve(self, record: SourceRecord) -> AbstractResult:
        """Return the best abstract available for `record`, or a real `unavailable`."""
        attempted: list[AbstractSource] = []
        skipped: list[AbstractSource] = []

        # Step 0: the record may already carry one from the search response that created
        # it. Re-fetching it would spend a rate-limited request to learn what we know.
        if record.abstract and record.abstract_source is AbstractSource.S2:
            return self._result(record.abstract, AbstractSource.S2, attempted, skipped)

        # Whether S2's two steps can run at all. `/paper/{id}` is on S2's search pool,
        # which is closed to unauthenticated clients — so without a key steps 1 and 3
        # are not steps that fail, they are steps that do not exist.
        s2_usable = self.s2.search_pool_available

        tldr_fallback: str | None = None
        if s2_usable:
            # Step 1 — S2's licensed abstract.
            attempted.append(AbstractSource.S2)
            text, source = await self.s2.get_abstract(record.source_id)
            if text and source is AbstractSource.S2:
                return self._result(text, source, attempted, skipped)
            # S2 may have answered with its TLDR already; hold it as the step-3 candidate
            # rather than returning it now — OpenAlex's full abstract outranks a one-liner.
            tldr_fallback = text if source is AbstractSource.TLDR else None
        else:
            # Skipped, not attempted, and emphatically not resolved. Calling anyway would
            # raise before step 2, taking down the one step that *would* have answered —
            # which is how a missing optional key turned into a dead review. Nothing is
            # converted to a result here, so HR-3 holds: the shortening is declared.
            skipped.append(AbstractSource.S2)

        # Step 2 — OpenAlex's inverted index, inverted. Unauthenticated, this is the
        # first step that runs rather than the second, and it is the reason the chain
        # still has real evidence to offer without an S2 key.
        attempted.append(AbstractSource.OPENALEX_INVERTED)
        text, source = await self.openalex.get_abstract(record.source_id)
        if text and source is AbstractSource.OPENALEX_INVERTED:
            return self._result(text, source, attempted, skipped)

        # Step 3 — the TLDR. Thin, generated, but real retrieved text. A TLDR already on
        # the stored record is usable whether or not S2 is reachable now: it came from a
        # response that did reach S2, so this half of the step survives the gate.
        if s2_usable:
            attempted.append(AbstractSource.TLDR)
        if tldr_fallback:
            return self._result(tldr_fallback, AbstractSource.TLDR, attempted, skipped)
        if record.abstract and record.abstract_source is AbstractSource.TLDR:
            return self._result(record.abstract, AbstractSource.TLDR, attempted, skipped)
        if not s2_usable:
            skipped.append(AbstractSource.TLDR)

        # Step 4 — the honest end of the chain.
        return self._result(None, AbstractSource.UNAVAILABLE, attempted, skipped)

    def _result(
        self,
        text: str | None,
        source: AbstractSource,
        attempted: list[AbstractSource],
        skipped: list[AbstractSource],
    ) -> AbstractResult:
        self.counts[source.value] += 1
        return AbstractResult(
            text=text, source=source, attempted=tuple(attempted), skipped=tuple(skipped)
        )

    @property
    def unavailable_rate(self) -> float:
        """Proportion of lookups that reached the end of the chain.

        Worth watching: a rate climbing towards 1.0 usually means a credential or a rate
        limit is failing quietly upstream, not that the literature stopped having
        abstracts.
        """
        total = sum(self.counts.values())
        return self.counts[AbstractSource.UNAVAILABLE.value] / total if total else 0.0
