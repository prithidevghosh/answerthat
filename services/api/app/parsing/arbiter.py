"""The arbiter. ADR-001 — the centre of gravity of the parsing pipeline.

    DOI present?        → Crossref /works/{doi}
    else title present? → S2 /paper/search/match
    else / S2 miss      → OpenAlex /works?search=

    agreement = 0.6·title_sim + 0.2·year_match + 0.2·first_author_sim
    accept at >= 0.85 → the external record REPLACES our parse as canonical CSL-JSON
                        (we retain our raw string and our parse for the audit view)

Why this exists: "did we parse this reference correctly?" is unanswerable without the
ground truth we are trying to produce. "Does this resolve to a real record that agrees
with what we extracted?" is answerable, and answering it gives us a self-healing parse,
a real linkable URL per reference, and a principled definition of "unparseable" that is
not a hand-tuned confidence cutoff.

**HR-1: this module never writes to `source_store`.** Provider adapters do that, from
real HTTP responses. We receive `SourceRecord`s that already exist there and hold their
`source_id`s. If you find yourself constructing a `SourceRecord` in this file, stop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.contracts import ConfidenceTier, ParsedReference, Provider, SourceRecord
from app.parsing.agreement import AgreementBreakdown, score_agreement

__all__ = ["ArbiterProviders", "Reconciliation", "Arbiter", "MatchPath"]

MatchPath = str  # "crossref_doi" | "s2_match" | "openalex_search" | "none"


@dataclass
class ArbiterProviders:
    """The three adapters, behind the Appendix A `Provider` protocol.

    All optional so the arbiter can run in a degraded *test* configuration, never a
    degraded production one — production wiring supplies all three, and a missing
    provider is recorded on the reconciliation rather than silently skipped.
    """

    crossref: Provider | None = None
    semantic_scholar: Provider | None = None
    openalex: Provider | None = None


@dataclass
class Reconciliation:
    """What happened to one reference, in enough detail to explain it to a user.

    `provisional_csl` is our own parse, retained even when the external record replaces
    it. CP-2 requires that the audit view can show both.
    """

    ref_id: str
    accepted: bool
    path: MatchPath
    agreement: AgreementBreakdown | None
    source_id: str | None
    provisional_csl: dict[str, Any] | None
    canonical_csl: dict[str, Any] | None
    raw_string: str
    considered: int = 0
    notes: list[str] = field(default_factory=list)
    # Providers that raised. Kept apart from `notes` on purpose: "we searched and found
    # nothing" and "we could not search" are different claims, and collapsing them is
    # exactly the false-negative-as-clean-bill-of-health that HR-3 exists to prevent.
    provider_errors: list[str] = field(default_factory=list)

    @property
    def fully_checked(self) -> bool:
        """False if any provider errored, i.e. an unresolved verdict here is provisional."""
        return not self.provider_errors


class Arbiter:
    """`accept_threshold` has no default on purpose.

    ADR-024: every threshold lives in `app/core/config.py` and appears nowhere else. A
    default here would be a second copy of `ARBITER_ACCEPT` — one that T1's sweep would
    not move, and that would silently take over the moment a caller forgot the argument.
    """

    def __init__(
        self,
        providers: ArbiterProviders,
        *,
        accept_threshold: float,
        max_concurrency: int = 4,
        candidates_per_search: int = 5,
    ) -> None:
        self.providers = providers
        self.accept_threshold = accept_threshold
        self.max_concurrency = max_concurrency
        self.candidates_per_search = candidates_per_search

    # ---------------------------------------------------------------- public API

    async def reconcile(
        self, references: Sequence[ParsedReference]
    ) -> tuple[list[ParsedReference], list[Reconciliation]]:
        """Reconcile a whole bibliography, batching where the providers allow it.

        Returns updated references and one `Reconciliation` per input, in input order.
        The two lists are the same length as the input — no reference is dropped, not
        even one that errored (HR-3).
        """
        hydrated = await self._batch_hydrate_dois(references)

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _one(reference: ParsedReference) -> Reconciliation:
            async with semaphore:
                return await self._reconcile_one(reference, hydrated)

        results = await asyncio.gather(*(_one(r) for r in references))
        updated = [self._apply(r, outcome) for r, outcome in zip(references, results, strict=True)]
        return updated, list(results)

    # ---------------------------------------------------------------- internals

    async def _batch_hydrate_dois(
        self, references: Sequence[ParsedReference]
    ) -> dict[str, SourceRecord]:
        """One batched call for every DOI we already hold, instead of N sequential ones.

        Semantic Scholar is ~1 req/s; a 60-reference bibliography looked up one at a
        time is a minute of wall clock before anything else can start. Batching is not
        an optimisation here, it is the difference between ingest finishing and ingest
        appearing to hang.
        """
        crossref = self.providers.crossref
        if crossref is None:
            return {}

        dois = sorted(
            {
                str(r.csl["DOI"]).strip().casefold()
                for r in references
                if r.csl and r.csl.get("DOI")
            }
        )
        if not dois:
            return {}

        records = await crossref.batch_hydrate(dois)
        by_doi: dict[str, SourceRecord] = {}
        for record in records:
            doi = str((record.csl or {}).get("DOI") or "").strip().casefold()
            if doi:
                by_doi[doi] = record
        return by_doi

    async def _reconcile_one(
        self, reference: ParsedReference, hydrated: dict[str, SourceRecord]
    ) -> Reconciliation:
        provisional = reference.csl
        notes: list[str] = []
        errors: list[str] = []
        doi = str((provisional or {}).get("DOI") or "").strip().casefold()
        title = str((provisional or {}).get("title") or "").strip()

        # --- 1. DOI → Crossref -------------------------------------------------
        if doi:
            record = hydrated.get(doi)
            if record is None and self.providers.crossref is not None:
                record = await self._resolve_doi(self.providers.crossref, doi, errors)
            if record is not None:
                breakdown = score_agreement(provisional, record.csl)
                if breakdown.accepted(self.accept_threshold):
                    return self._accepted(reference, "crossref_doi", breakdown, record, provisional, notes, errors, 1)
                notes.append(
                    f"Crossref returned a record for this DOI but it scored "
                    f"{breakdown.score:.2f}, below {self.accept_threshold:.2f}"
                )
            elif self.providers.crossref is None:
                notes.append("no Crossref provider configured; the DOI path was not tried")

        # --- 2. title → Semantic Scholar /paper/search/match -------------------
        best: tuple[AgreementBreakdown, SourceRecord] | None = None
        considered = 0

        if title and self.providers.semantic_scholar is not None:
            year = _year_of(provisional)
            try:
                record = await self.providers.semantic_scholar.match_reference(title, year)
            except Exception as exc:  # noqa: BLE001 - recorded on the result, never swallowed
                errors.append(f"Semantic Scholar match failed: {exc!r}")
                record = None
            if record is not None:
                considered += 1
                breakdown = score_agreement(provisional, record.csl)
                best = (breakdown, record)
                if breakdown.accepted(self.accept_threshold):
                    return self._accepted(reference, "s2_match", breakdown, record, provisional, notes, errors, considered)
                notes.append(f"S2 best match scored {breakdown.score:.2f}")
        elif title:
            notes.append("no Semantic Scholar provider configured; the title path was not tried")

        # --- 3. OpenAlex search (also the fallback for an S2 miss) -------------
        query = title or reference.raw_string.strip()
        if query and self.providers.openalex is not None:
            try:
                records = await self.providers.openalex.search_works(
                    query, limit=self.candidates_per_search
                )
            except Exception as exc:  # noqa: BLE001 - recorded on the result, never swallowed
                errors.append(f"OpenAlex search failed: {exc!r}")
                records = []
            considered += len(records)
            for record in records:
                breakdown = score_agreement(provisional, record.csl)
                if best is None or breakdown.score > best[0].score:
                    best = (breakdown, record)
            if best and best[0].accepted(self.accept_threshold):
                return self._accepted(
                    reference, "openalex_search", best[0], best[1], provisional, notes, errors, considered
                )
        elif query:
            notes.append("no OpenAlex provider configured; the search path was not tried")

        if best is not None:
            notes.append(
                f"best candidate scored {best[0].score:.2f}, below the {self.accept_threshold:.2f} "
                "acceptance threshold, so our own parse is kept"
            )
        elif considered == 0 and not errors:
            notes.append("no external candidates were returned for this reference")
        elif errors:
            notes.append(
                "this reference is unresolved because a provider failed, not because nothing "
                "matched it — the lookup did not complete"
            )

        return Reconciliation(
            ref_id=reference.ref_id,
            accepted=False,
            path="none",
            agreement=best[0] if best else None,
            source_id=None,
            provisional_csl=provisional,
            canonical_csl=provisional,
            raw_string=reference.raw_string,
            considered=considered,
            notes=notes,
            provider_errors=errors,
        )

    async def _resolve_doi(
        self, provider: Provider, doi: str, errors: list[str]
    ) -> SourceRecord | None:
        """Look a DOI up on the Crossref adapter.

        Appendix A's `Provider` protocol has no DOI method, so the protocol-legal route
        is `search_works(doi)`. B2's Crossref adapter also exposes `resolve_doi(doi)`,
        which hits `/works/{doi}` directly — an exact lookup rather than a search, and
        the path ADR-001 actually names. We prefer it when it is there and fall back to
        the protocol otherwise, so this stays runnable against any conforming provider
        (the test stubs implement only the protocol).
        """
        resolve = getattr(provider, "resolve_doi", None)
        try:
            if callable(resolve):
                return await resolve(doi)
            records = await provider.search_works(doi, limit=1)
        except Exception as exc:  # noqa: BLE001 - recorded on the result, never swallowed
            errors.append(f"crossref lookup failed: {exc!r}")
            return None
        return records[0] if records else None

    def _accepted(
        self,
        reference: ParsedReference,
        path: MatchPath,
        breakdown: AgreementBreakdown,
        record: SourceRecord,
        provisional: dict[str, Any] | None,
        notes: list[str],
        errors: list[str],
        considered: int,
    ) -> Reconciliation:
        return Reconciliation(
            ref_id=reference.ref_id,
            accepted=True,
            path=path,
            agreement=breakdown,
            source_id=record.source_id,
            provisional_csl=provisional,
            # The external record replaces our parse as canonical (ADR-001). Our parse
            # and the raw string both survive on this object for the audit view.
            canonical_csl=dict(record.csl),
            raw_string=reference.raw_string,
            considered=considered,
            notes=notes,
            provider_errors=errors,
        )

    def _apply(self, reference: ParsedReference, outcome: Reconciliation) -> ParsedReference:
        """Fold a reconciliation back into the reference. Never drops one."""
        if outcome.accepted:
            return reference.model_copy(
                update={
                    "csl": outcome.canonical_csl,
                    "tier": ConfidenceTier.RESOLVED,
                    "agreement_score": outcome.agreement.score if outcome.agreement else None,
                    "source_id": outcome.source_id,
                }
            )
        return reference.model_copy(
            update={
                "agreement_score": outcome.agreement.score if outcome.agreement else None,
                # An entry we could not resolve keeps whatever tier its parse earned.
                # It is never promoted for having been looked at.
                "tier": reference.tier,
            }
        )


def _year_of(csl: dict[str, Any] | None) -> int | None:
    parts = ((csl or {}).get("issued") or {}).get("date-parts") if csl else None
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        try:
            return int(parts[0][0])
        except (TypeError, ValueError):
            return None
    return None
