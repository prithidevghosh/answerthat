"""Quote-backed entailment with a mechanical substring check. ADR-006.

**One verifier, two callers.** Pointed at a *candidate* it answers "is this missing work
the author should have cited?"; pointed at an *existing anchor* it answers "does the
source they cited actually support this claim?". Identical code, identical evidence
format — the difference is only which `source_id` is passed in, which is why the kernel
and the review feed can be held to the same standard of evidence.

The load-bearing mechanism is not the prompt. Every non-`unverifiable` verdict must
carry a **verbatim quote from the fetched abstract**, and this module then checks
mechanically that the quote is actually in that abstract. If it is not, the finding
dies. A verifier that must quote its evidence cannot fabricate support without producing
a string we can falsify with `in` — no prompt improvement substitutes for that, and no
prompt can talk past it.

The other half of the honesty contract is the fourth outcome. If no abstract survives
the fallback chain (S2 → OpenAlex inverted → S2 TLDR), `unverifiable_no_abstract` is the
**only** legal output, and it is displayed rather than skipped. Verifying against a title
instead would produce a confident verdict from no evidence, which is worse than saying
we could not check.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.core.contracts import (
    AbstractSource,
    LLMRole,
    SourceRecord,
    Verification,
    VerificationLabel,
)
from app.providers.abstracts import AbstractResolver
from app.review.llm import ReviewLLM, object_schema
from app.review.prompts import VERIFIER_SYSTEM

__all__ = ["Verifier", "VerificationOutcome", "quote_is_present", "VERIFIER_SYSTEM"]

_WS = re.compile(r"\s+")
_QUOTE_MAP = {
    "’": "'", "‘": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
    "…": "...",
}

#: Below this many characters a "quote" is a fragment that would match almost any
#: abstract, which makes the substring check meaningless rather than mechanical.
MIN_QUOTE_CHARS = 25


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text or "")
    for fancy, plain in _QUOTE_MAP.items():
        folded = folded.replace(fancy, plain)
    return _WS.sub(" ", folded).strip().casefold()


def quote_is_present(quote: str, abstract: str) -> bool:
    """The mechanical check. Whitespace- and punctuation-normalized substring match.

    Normalization is deliberately narrow: Unicode form, curly quotes, dash variants, and
    whitespace runs — the ways the same characters get re-encoded between a provider's
    JSON and a model's output. It does **not** stem, drop stopwords, or fuzzy-match,
    because every one of those would let a paraphrase pass as a quote, and a paraphrase
    that passes is exactly the fabrication this check exists to catch.
    """
    if not quote or not abstract:
        return False
    if len(quote.strip()) < MIN_QUOTE_CHARS:
        return False
    return _normalize(quote) in _normalize(abstract)


@dataclass(frozen=True)
class VerificationOutcome:
    """The verifier's full result, including why a verdict was thrown away.

    `verification` is `None` only when the quote check failed. That is not an error and
    not a verdict — it is the mechanism working, and it is counted so the proportion of
    killed findings is visible in progress rather than inferred from a short feed.
    """

    verification: Verification | None
    quote_check_failed: bool = False
    rejected_quote: str | None = None

    @property
    def usable(self) -> bool:
        return self.verification is not None


VERIFIER_SCHEMA = object_schema(
    {
        "label": {
            "type": "string",
            "enum": ["supports", "partially_supports", "does_not_address", "contradicts"],
        },
        "quote": {
            "type": "string",
            "description": "A verbatim contiguous span copied from the abstract",
        },
        "confidence": {"type": "number", "description": "0.0 to 1.0"},
    }
)


class Verifier:
    """The single verification stage. Serves review and the edit path alike."""

    def __init__(
        self, *, llm: ReviewLLM, abstracts: AbstractResolver, source_store: Any
    ) -> None:
        self.llm = llm
        self.abstracts = abstracts
        # Read-only here. `app/review/` never writes to the store — it verifies records
        # a provider adapter already wrote from a real HTTP response (HR-1).
        self.store = source_store
        self.verified = 0
        self.quote_check_failures = 0
        self.unverifiable = 0

    async def verify_detailed(
        self, claim_text: str, record: SourceRecord, *, doc_id: str = ""
    ) -> VerificationOutcome:
        """Full result, including quote-check kills. Used by the review stream."""
        resolved = await self.abstracts.resolve(record)
        if not resolved.available or not resolved.text:
            self.unverifiable += 1
            return VerificationOutcome(
                verification=Verification(
                    label=VerificationLabel.UNVERIFIABLE_NO_ABSTRACT,
                    quote=None,
                    abstract_source=AbstractSource.UNAVAILABLE,
                    confidence=1.0,
                )
            )

        abstract = resolved.text
        # LLMRole.VERIFY routes to the strongest model in the table (ADR-015). This is
        # the judgment the product's honesty rests on and is deliberately not
        # economised: the cost control is the cascade upstream — embedding prefilter,
        # then the mini reranker — which is what holds this call to VERIFY_KEEP per claim.
        payload = await self.llm.complete_json(
            role=LLMRole.VERIFY,
            system=VERIFIER_SYSTEM,
            prompt=_build_prompt(claim_text, record, abstract),
            schema=VERIFIER_SCHEMA,
            doc_id=doc_id,
        )

        quote = str(payload.get("quote") or "")
        if not quote_is_present(quote, abstract):
            # The verdict cannot be evidenced. The finding dies here — this is the
            # single most important branch in the review pipeline.
            self.quote_check_failures += 1
            return VerificationOutcome(
                verification=None, quote_check_failed=True, rejected_quote=quote[:300]
            )

        self.verified += 1
        return VerificationOutcome(
            verification=Verification(
                label=VerificationLabel(str(payload.get("label"))),
                quote=quote,
                abstract_source=resolved.source,
                confidence=min(1.0, max(0.0, float(payload.get("confidence") or 0.0))),
            )
        )

    async def verify(self, claim_text: str, source_id: str) -> Verification:
        """The `VerificationService` port B3's kernel and edit path call.

        A quote-check failure is reported as `does_not_address` with confidence 0.0 and
        no quote — the fail-closed direction. The model asserted support it could not
        evidence, so the accurate statement is that no verifiable evidence in this
        abstract addresses the claim; anything stronger would let an unquotable
        assertion satisfy the kernel's REJECT rule 3.
        """
        record = await self.store.fetch(source_id)
        if record is None:
            raise KeyError(
                f"source_id {source_id!r} is not in the source store. Verification is "
                "only performed against records a provider adapter wrote (HR-1)."
            )
        outcome = await self.verify_detailed(claim_text, record)
        if outcome.verification is not None:
            return outcome.verification
        return Verification(
            label=VerificationLabel.DOES_NOT_ADDRESS,
            quote=None,
            abstract_source=AbstractSource.UNAVAILABLE,
            confidence=0.0,
        )

    def snapshot(self) -> dict[str, int]:
        return {
            "verified": self.verified,
            "quote_check_failures": self.quote_check_failures,
            "unverifiable_no_abstract": self.unverifiable,
        }


def _build_prompt(claim_text: str, record: SourceRecord, abstract: str) -> str:
    csl = record.csl
    issued = ((csl.get("issued") or {}).get("date-parts") or [[None]])[0]
    return "\n".join(
        [
            "CLAIM FROM THE MANUSCRIPT:",
            claim_text,
            "",
            "CANDIDATE PAPER:",
            f"title: {csl.get('title', '(untitled)')}",
            f"year: {issued[0] if issued else '(unknown)'}",
            "",
            "ABSTRACT (your quote must be copied verbatim from between these markers):",
            "<<<ABSTRACT",
            abstract,
            "ABSTRACT>>>",
        ]
    )
