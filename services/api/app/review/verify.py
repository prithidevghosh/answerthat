"""`VerificationService` — the port B3's kernel and edit path call. ADR-006.

A thin factory over `Verifier`, at the module path B3's Interface Request named. The
verifier itself is unchanged: the same code, the same quote-backed evidence format, and
the same mechanical substring check that serves the review feed.

The one behaviour to know at this boundary: a quote-check kill comes back as
`does_not_address` with `quote=None` and `confidence=0.0`, not as an exception and not as
`SUPPORTS`. That is the fail-closed direction. REJECT rule 3 requires a
`SUPPORTS`/`PARTIALLY_SUPPORTS` verification before a newly asserted claim is allowed, so
an assertion the model could not evidence must not be able to satisfy it.
"""

from __future__ import annotations

from app.core.config import Settings
from app.review.composition import get_verifier
from app.review.verifier import Verifier

__all__ = ["get_verification_service", "Verifier"]

_service: Verifier | None = None


def get_verification_service(settings: Settings | None = None) -> Verifier:
    """Process-wide verifier. Satisfies `VerificationService.verify(claim_text, source_id)`."""
    global _service
    if _service is None:
        _service = get_verifier(settings)
    return _service


def reset() -> None:
    """Tests only."""
    global _service
    _service = None
