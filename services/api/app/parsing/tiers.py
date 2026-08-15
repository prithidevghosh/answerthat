"""Confidence tiers, and the invariant that no reference is ever dropped.

Five tiers, and every reference GROBID detected lands in exactly one of them:

* `resolved` — the arbiter matched it to an external record at ≥ 0.85, and that record
  is now the canonical CSL-JSON.
* `parsed_unresolved` — we parsed it well, nothing external agreed. A real reference we
  cannot link. Displayed as such.
* `low_confidence` — parsed poorly and unresolved. Shown with a warning.
* `quarantined` — we could not make a usable record of it at all. **The raw string is
  kept verbatim** and shown to the user, because the reference exists in their paper
  whether or not we understood it.
* `orphan_marker` — describes an in-text marker with no bibliography entry behind it,
  not a reference; counted separately for exactly that reason.

The arithmetic in `TierCounts.assert_invariant` is the point of this module. A reference
that quietly disappears between GROBID and the UI is the failure HR-3 exists to prevent,
and it is the kind that looks like success.
"""

from __future__ import annotations

from typing import Any

from app.core.contracts import ConfidenceTier, ParsedReference

__all__ = ["initial_tier", "is_usable", "tier_for", "MIN_USABLE_FIELDS"]

# A record is usable if we can do something with it: match it externally, or show it to
# a human as a reference rather than as a fragment.
MIN_USABLE_FIELDS = ("title", "DOI")


def is_usable(csl: dict[str, Any] | None) -> bool:
    """Whether this parse gives the arbiter anything to work with.

    A DOI is enough on its own. A title is enough on its own. Neither means the entry
    cannot be looked up and cannot be displayed as a reference — that is quarantine.
    """
    if not csl:
        return False
    return any(csl.get(field) for field in MIN_USABLE_FIELDS)


def initial_tier(
    csl: dict[str, Any] | None,
    parse_confidence: float,
    raw_string: str,
    *,
    threshold: float,
) -> ConfidenceTier:
    """The tier a reference sits in *before* the arbiter runs.

    Nothing is `resolved` at this point: resolution means an external record agreed with
    us, and no external call has happened yet. Calling a well-parsed entry "resolved"
    because it looks tidy would be exactly the overclaim the tiers exist to prevent.
    """
    if not is_usable(csl):
        return ConfidenceTier.QUARANTINED
    if parse_confidence < threshold:
        return ConfidenceTier.LOW_CONFIDENCE
    return ConfidenceTier.PARSED_UNRESOLVED


def tier_for(
    reference: ParsedReference,
    *,
    threshold: float,
    accept_threshold: float,
) -> ConfidenceTier:
    """The tier a reference sits in after arbitration, given its agreement score."""
    if reference.agreement_score is not None and reference.agreement_score >= accept_threshold:
        if not reference.source_id:
            # Accepting a match without recording which record we matched would leave a
            # "resolved" reference pointing at nothing. HR-1 says the id is the proof.
            raise ValueError(
                f"reference {reference.ref_id!r} scored {reference.agreement_score} but carries "
                "no source_id; a resolved reference must name the record that resolved it"
            )
        return ConfidenceTier.RESOLVED
    return initial_tier(reference.csl, reference.parse_confidence, reference.raw_string, threshold=threshold)
