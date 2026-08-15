"""HR-2 / ADR-010: the app must not start without both keys.

The point of these tests is not that a validator exists. It is that there is no path —
no default, no empty string, no whitespace, no "anonymous mode" flag — by which the
application can come up without real credentials.
"""

from __future__ import annotations

import pytest

from app.core.config import (
    REQUIRED_KEYS,
    Settings,
    get_settings,
    missing_required_keys,
    reset_settings_cache,
)
from app.core.contracts import MissingAPIKeyError

BOTH_KEYS = {
    "SEMANTIC_SCHOLAR_API_KEY": "s2-test-key",
    "OPENALEX_API_KEY": "oa-test-key",
}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Neutralise the developer's real environment so these tests mean the same in CI."""
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def _settings(**env):
    return Settings(_env_file=None, **env)


def test_both_keys_present_starts() -> None:
    s = _settings(semantic_scholar_api_key="a", openalex_api_key="b")
    assert s.semantic_scholar_api_key == "a"


@pytest.mark.parametrize("missing", list(REQUIRED_KEYS))
def test_absent_key_aborts_startup(missing: str) -> None:
    env = {k.lower(): v for k, v in BOTH_KEYS.items()}
    env.pop(missing.lower())
    with pytest.raises(MissingAPIKeyError) as exc:
        _settings(**env)
    assert missing in str(exc.value)


@pytest.mark.parametrize("empty_value", ["", "   ", "\t", "\n"])
@pytest.mark.parametrize("key", list(REQUIRED_KEYS))
def test_empty_or_whitespace_key_aborts_startup(key: str, empty_value: str) -> None:
    """'Absent or empty' — a whitespace key is not a key."""
    env = {k.lower(): v for k, v in BOTH_KEYS.items()}
    env[key.lower()] = empty_value
    with pytest.raises(MissingAPIKeyError):
        _settings(**env)


def test_both_missing_names_both() -> None:
    with pytest.raises(MissingAPIKeyError) as exc:
        _settings()
    message = str(exc.value)
    for key in REQUIRED_KEYS:
        assert key in message


def test_error_message_says_which_key_and_where_to_get_it() -> None:
    """A human reading this message must not have to grep the codebase."""
    with pytest.raises(MissingAPIKeyError) as exc:
        _settings(openalex_api_key="b")
    message = str(exc.value)
    assert "SEMANTIC_SCHOLAR_API_KEY" in message
    assert "semanticscholar.org" in message
    assert ".env" in message
    # And it must explain *why*, so nobody "helpfully" adds a fallback later.
    assert "no missing work found" in message


def test_missing_required_keys_reports_without_raising() -> None:
    assert missing_required_keys({}) == list(REQUIRED_KEYS)
    assert missing_required_keys({**BOTH_KEYS}) == []
    assert missing_required_keys({**BOTH_KEYS, "OPENALEX_API_KEY": " "}) == ["OPENALEX_API_KEY"]


def test_get_settings_raises_when_env_is_unset(monkeypatch) -> None:
    """The failure happens at startup, not on the first request."""
    monkeypatch.setattr("app.core.config.Settings", lambda: _settings())
    reset_settings_cache()
    with pytest.raises(MissingAPIKeyError):
        get_settings()


def test_thresholds_match_the_adrs() -> None:
    """ADR-001 fixes 0.85; ADR-011 fixes 0.05. Drift here changes what 'resolved' means."""
    s = _settings(**{k.lower(): v for k, v in BOTH_KEYS.items()})
    assert s.arbiter_accept_threshold == 0.85
    assert s.style_ambiguity_margin == 0.05
