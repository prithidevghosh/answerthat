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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.contracts import AbstractSource, SourceRecord

__all__ = ["AbstractResolver", "AbstractResult", "AbstractCapableProvider"]


class AbstractCapableProvider(Protocol):
    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]: ...


@dataclass(frozen=True)
class AbstractResult:
    """What the chain found, and where it stopped.

    `attempted` is kept so the UI can say *which* steps were tried rather than only that
    the result was unavailable — HR-3's "the system says it doesn't know" reads better
    with the shape of the not-knowing attached.
    """

    text: str | None
    source: AbstractSource
    attempted: tuple[AbstractSource, ...]

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
        semantic_scholar: AbstractCapableProvider,
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

        # Step 0: the record may already carry one from the search response that created
        # it. Re-fetching it would spend a rate-limited request to learn what we know.
        if record.abstract and record.abstract_source is AbstractSource.S2:
            return self._result(record.abstract, AbstractSource.S2, attempted)

        # Step 1 — S2's licensed abstract.
        attempted.append(AbstractSource.S2)
        text, source = await self.s2.get_abstract(record.source_id)
        if text and source is AbstractSource.S2:
            return self._result(text, source, attempted)
        # S2 may have answered with its TLDR already; hold it as the step-3 candidate
        # rather than returning it now — OpenAlex's full abstract outranks a one-liner.
        tldr_fallback = text if source is AbstractSource.TLDR else None

        # Step 2 — OpenAlex's inverted index, inverted.
        attempted.append(AbstractSource.OPENALEX_INVERTED)
        text, source = await self.openalex.get_abstract(record.source_id)
        if text and source is AbstractSource.OPENALEX_INVERTED:
            return self._result(text, source, attempted)

        # Step 3 — the TLDR. Thin, generated, but real retrieved text.
        attempted.append(AbstractSource.TLDR)
        if tldr_fallback:
            return self._result(tldr_fallback, AbstractSource.TLDR, attempted)
        if record.abstract and record.abstract_source is AbstractSource.TLDR:
            return self._result(record.abstract, AbstractSource.TLDR, attempted)

        # Step 4 — the honest end of the chain.
        return self._result(None, AbstractSource.UNAVAILABLE, attempted)

    def _result(
        self, text: str | None, source: AbstractSource, attempted: list[AbstractSource]
    ) -> AbstractResult:
        self.counts[source.value] += 1
        return AbstractResult(text=text, source=source, attempted=tuple(attempted))

    @property
    def unavailable_rate(self) -> float:
        """Proportion of lookups that reached the end of the chain.

        Worth watching: a rate climbing towards 1.0 usually means a credential or a rate
        limit is failing quietly upstream, not that the literature stopped having
        abstracts.
        """
        total = sum(self.counts.values())
        return self.counts[AbstractSource.UNAVAILABLE.value] / total if total else 0.0
