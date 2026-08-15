"""Credential enforcement for provider construction. HR-2 / ADR-010.

This is the highest-stakes module in `app/providers/`. Read the reasoning before
changing anything in it.

Under anonymous or unauthenticated limits, neither Semantic Scholar nor OpenAlex fails
loudly. They return **thin or empty result sets**. The review pipeline cannot tell a
throttled empty result from a genuinely empty literature, so it would faithfully report
the first as *"no missing work found"* — a false negative wearing a clean bill of health.
That is strictly worse than a crash, because it is invisible to the researcher who is
trusting it.

So the design is not "warn and continue". It is: **make the misconfiguration impossible
to run.** Every provider constructor calls `require_key()`. There is no anonymous path,
no default route, no degraded mode, and no code path that catches `MissingAPIKeyError`
in order to keep going.

If you are here because a test or a demo is inconvenient without a key: the fix is a
fake key plus a stubbed transport, never a relaxation here.
"""

from __future__ import annotations

from app.core.contracts import MissingAPIKeyError

__all__ = ["require_key", "require_mailto", "redact"]

# Where a human actually obtains each credential. A refusal that does not tell the
# operator how to fix it just converts one dead end into another.
_WHERE: dict[str, str] = {
    "SEMANTIC_SCHOLAR_API_KEY": "free — request at https://www.semanticscholar.org/product/api",
    "OPENALEX_API_KEY": "free — register at https://openalex.org (keys mandatory since 2026-02-13)",
    "OPENALEX_MAILTO": "your contact email address — required for the polite pool",
    "ANTHROPIC_API_KEY": "https://console.anthropic.com/settings/keys",
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
