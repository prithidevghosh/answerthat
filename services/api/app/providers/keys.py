"""Credential enforcement for provider construction. HR-2 / ADR-010 (amended, ADR-010a).

This is the highest-stakes module in `app/providers/`. Read the reasoning before
changing anything in it.

The invariant being protected is **not** "every provider has a key". It is: *a throttled
search must never reach the review pipeline disguised as an empty literature.* A pipeline
that cannot tell those apart reports a false negative as a clean bill of health, which is
strictly worse than a crash because it is invisible to the researcher trusting it.

Which enforcement that invariant requires depends on how the provider fails:

* **OpenAlex** degrades silently. Anonymous access is 100 credits/day and a list query
  costs 10, so a review's third search returns a thin result rather than an error. The
  only safe design there is to make the misconfiguration impossible to run —
  `require_key()`, no anonymous path, no degraded mode.
* **Semantic Scholar** degrades *loudly*. Unauthenticated traffic is served from a shared
  pool and throttling arrives as **HTTP 429**, which `ProviderHTTP` retries with backoff
  and then raises as `ProviderRateLimited` rather than converting to `[]`. The invariant
  is already enforced on the response path, so a startup gate adds no safety — it only
  blocks operators who cannot obtain a key, and since 2024-09 Semantic Scholar approves
  neither free-domain-email nor third-party-app key requests. Its key is therefore
  **optional**: `optional_key()`, used for the dedicated 1 RPS allowance when present.

The distinction to keep in mind when adding a provider: ask *how does this API tell us it
is throttling us?* Silent thinning ⇒ `require_key`. A 4xx we already raise on ⇒
`optional_key`. Never relax `require_key` for a provider that fails quietly.

If you are here because a test or a demo is inconvenient without a key: the fix is a
fake key plus a stubbed transport, never a relaxation here.
"""

from __future__ import annotations

from app.core.contracts import MissingAPIKeyError

__all__ = ["require_key", "optional_key", "require_mailto", "redact"]

# Where a human actually obtains each credential. A refusal that does not tell the
# operator how to fix it just converts one dead end into another.
_WHERE: dict[str, str] = {
    "SEMANTIC_SCHOLAR_API_KEY": "free — request at https://www.semanticscholar.org/product/api",
    "OPENALEX_API_KEY": "free — register at https://openalex.org (keys mandatory since 2026-02-13)",
    "OPENALEX_MAILTO": "your contact email address — required for the polite pool",
    "OPENAI_API_KEY": "https://platform.openai.com/api-keys (ADR-015 — every LLM role uses it)",
}


def _explain(env_var: str, provider: str, consequence: str) -> str:
    where = _WHERE.get(env_var, "see .env.example")
    return "\n".join(
        [
            "",
            f"{provider} cannot be constructed: {env_var} is missing or empty.",
            f"           {where}",
            "",
            f"Why this raises instead of degrading: {consequence}",
            "",
            "This is HR-2 / ADR-010 and it is deliberate. There is no anonymous mode.",
            "",
        ]
    )


def require_key(value: str | None, *, env_var: str, provider: str) -> str:
    """Return a non-empty API key, or raise `MissingAPIKeyError`.

    Whitespace-only is treated as absent — `OPENALEX_API_KEY=" "` in a `.env` is a
    misconfiguration, not a credential.
    """
    key = (value or "").strip()
    if not key:
        raise MissingAPIKeyError(
            _explain(
                env_var,
                provider,
                "without a key this API does not error, it returns thin or empty "
                "results, which this system would report to a researcher as "
                '"no missing work found".',
            )
        )
    return key


def optional_key(value: str | None) -> str | None:
    """Return a non-empty API key, or `None` when none was configured.

    For providers whose throttling surfaces as an HTTP error we already raise on, so
    running unauthenticated cannot produce a silent false negative (ADR-010a). Whitespace
    is treated as absent, exactly as in `require_key` — `KEY=" "` is a misconfiguration in
    either regime, and here it means "unauthenticated" rather than a key of one space.

    This is deliberately not a keyword on any constructor. There is no `allow_anonymous`
    flag to find, because authentication is not a mode the caller selects: it follows from
    whether a credential exists.
    """
    key = (value or "").strip()
    return key or None


def require_mailto(value: str | None, *, env_var: str, provider: str) -> str:
    """Return a non-empty contact address, or raise `MissingAPIKeyError`.

    Not a secret, but enforced with the same severity and the same error type. Traffic
    without a contact address is served outside the polite pool, where throttling
    presents as sparse results rather than as an error — the identical failure mode a
    missing key produces, so it gets the identical treatment.
    """
    mailto = (value or "").strip()
    if not mailto or "@" not in mailto:
        raise MissingAPIKeyError(
            _explain(
                env_var,
                provider,
                "calls without a contact address are served outside the polite pool, "
                "where throttling presents as sparse results rather than as an error — "
                "the same invisible false negative a missing key produces.",
            )
        )
    return mailto


def redact(secret: str) -> str:
    """A stable, non-reversible hint for logs. Never log a raw key."""
    if not secret:
        return "<empty>"
    return f"{secret[:2]}…{secret[-2:]}" if len(secret) > 8 else "<set>"
