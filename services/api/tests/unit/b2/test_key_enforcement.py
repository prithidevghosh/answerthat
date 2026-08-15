"""HR-2 / ADR-010 — credentials are required, and their absence raises.

These tests exist to make softening this expensive. If one of them starts failing
because a provider grew an anonymous path, the correct response is to delete the path,
not the test. The failure mode being prevented is not a crash — it is a *review that
reports "no missing work found" because it was silently throttled*.
"""

from __future__ import annotations

import pytest

from app.core.contracts import MissingAPIKeyError
from app.providers.keys import redact, require_key, require_mailto


@pytest.mark.parametrize("absent", [None, "", "   ", "\t\n"])
def test_require_key_raises_when_absent_or_blank(absent: str | None) -> None:
    with pytest.raises(MissingAPIKeyError) as exc:
        require_key(absent, env_var="SEMANTIC_SCHOLAR_API_KEY", provider="SemanticScholarProvider")
    message = str(exc.value)
    assert "SEMANTIC_SCHOLAR_API_KEY" in message
    assert "SemanticScholarProvider" in message
    # The operator must be told where to get it, or the refusal is a dead end.
    assert "semanticscholar.org" in message
    # And why it is not merely a warning.
    assert "no missing work found" in message


def test_require_key_returns_stripped_value() -> None:
    assert require_key("  abc123 ", env_var="OPENALEX_API_KEY", provider="OpenAlexProvider") == "abc123"


@pytest.mark.parametrize("bad", [None, "", "   ", "not-an-email"])
def test_require_mailto_raises_without_a_contact_address(bad: str | None) -> None:
    with pytest.raises(MissingAPIKeyError) as exc:
        require_mailto(bad, env_var="OPENALEX_MAILTO", provider="OpenAlexProvider")
    assert "polite pool" in str(exc.value)


def test_require_mailto_accepts_an_address() -> None:
    assert require_mailto(" a@b.org ", env_var="OPENALEX_MAILTO", provider="OpenAlexProvider") == "a@b.org"


def test_missing_key_error_is_not_a_subclass_of_anything_commonly_caught() -> None:
    """`except Exception` in a provider loop must not be able to swallow this by accident.

    It is a `RuntimeError` per Appendix A, so a bare `except Exception` *would* catch it.
    This test pins the type so that if anyone reclassifies it, the change is deliberate
    and reviewed — and documents that no `except` in this package may catch it.
    """
    assert issubclass(MissingAPIKeyError, RuntimeError)
    assert not issubclass(MissingAPIKeyError, ValueError)


def test_redact_never_reveals_the_key() -> None:
    secret = "sk-abcdefghijklmnop"
    assert secret not in redact(secret)
    assert redact("") == "<empty>"
    assert redact("short") == "<set>"
