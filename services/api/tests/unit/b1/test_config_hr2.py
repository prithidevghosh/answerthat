"""HR-2 / ADR-010 / ADR-010a: the app must not start without the keys that fail quietly.

The point of these tests is not that a validator exists. It is that there is no path —
no default, no empty string, no whitespace, no "anonymous mode" flag — by which the
application can come up without the credentials whose absence would be *invisible*.

ADR-010a removed `SEMANTIC_SCHOLAR_API_KEY` from that set, on the evidence that S2
throttles with a 429 the provider layer raises on. So this module also pins the other
direction: that an absent S2 key does **not** abort startup, and is reported instead.
"""

from __future__ import annotations

import pytest

from app.core.config import (
    OPTIONAL_KEYS,
    REQUIRED_KEYS,
    Settings,
    get_settings,
    missing_required_keys,
    reset_settings_cache,
    unauthenticated_providers,
)
from app.core.contracts import MissingAPIKeyError

# ADR-015 made OPENAI_API_KEY startup-fatal on the same terms as OpenAlex.
ALL_KEYS = {
    "OPENALEX_API_KEY": "oa-test-key",
    "OPENAI_API_KEY": "sk-test-key",
}
BOTH_KEYS = ALL_KEYS  # kept as an alias; the name predates several revisions


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Neutralise the developer's real environment so these tests mean the same in CI."""
    for name in (*ALL_KEYS, *OPTIONAL_KEYS):
        monkeypatch.delenv(name, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def _settings(**env):
    return Settings(_env_file=None, **env)


def test_all_keys_present_starts() -> None:
    s = _settings(**{k.lower(): v for k, v in ALL_KEYS.items()})
    assert s.openalex_api_key == "oa-test-key"
    assert s.openai_api_key == "sk-test-key"


# --------------------------------------------------------------------------- ADR-010a


@pytest.mark.parametrize("absent", ["", "   ", "\t"])
def test_an_absent_semantic_scholar_key_does_not_abort_startup(absent: str) -> None:
    """ADR-010a. The safety property is enforced on the 429 path, not at startup — see
    `b2/test_semantic_scholar.py::…_429_raises_rather_than_returning_an_empty_result`.
    Restoring the gate here without deleting that test would be enforcing nothing twice.
    """
    s = _settings(**{k.lower(): v for k, v in ALL_KEYS.items()}, semantic_scholar_api_key=absent)
    assert s.semantic_scholar_api_key == absent


def test_the_semantic_scholar_key_is_used_when_it_is_supplied() -> None:
    s = _settings(**{k.lower(): v for k, v in ALL_KEYS.items()}, semantic_scholar_api_key="s2-key")
    assert s.semantic_scholar_api_key == "s2-key"


def test_an_unauthenticated_provider_is_reported_rather_than_left_to_be_inferred() -> None:
    """HR-3 applies to configuration too: running in the shared pool is a fact the
    operator should read in the logs, not deduce from latency."""
    assert unauthenticated_providers({}) == ["SEMANTIC_SCHOLAR_API_KEY"]
    assert unauthenticated_providers({"SEMANTIC_SCHOLAR_API_KEY": "  "}) == [
        "SEMANTIC_SCHOLAR_API_KEY"
    ]
    assert unauthenticated_providers({"SEMANTIC_SCHOLAR_API_KEY": "k"}) == []


def test_the_optional_key_is_never_startup_fatal() -> None:
    """A guard on the split itself, so that moving a key between the two dicts is a
    deliberate edit to this test rather than a silent change in startup behaviour."""
    assert set(REQUIRED_KEYS) == {"OPENALEX_API_KEY", "OPENAI_API_KEY"}
    assert set(OPTIONAL_KEYS) == {"SEMANTIC_SCHOLAR_API_KEY"}
    assert not (set(REQUIRED_KEYS) & set(OPTIONAL_KEYS))


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


def test_all_missing_names_every_required_key() -> None:
    with pytest.raises(MissingAPIKeyError) as exc:
        _settings()
    message = str(exc.value)
    for key in REQUIRED_KEYS:
        assert key in message


def test_error_message_says_which_key_and_where_to_get_it() -> None:
    """A human reading this message must not have to grep the codebase."""
    env = {k.lower(): v for k, v in ALL_KEYS.items()}
    env.pop("openalex_api_key")
    with pytest.raises(MissingAPIKeyError) as exc:
        _settings(**env)
    message = str(exc.value)
    assert "OPENALEX_API_KEY" in message
    assert "openalex.org" in message
    assert ".env" in message
    # And it must explain *why*, so nobody "helpfully" adds a fallback later.
    assert "no missing work found" in message


def test_the_abort_message_says_the_s2_key_is_not_the_problem() -> None:
    """The message an operator hits at 2am. Before ADR-010a it sent them to request a key
    S2 would not issue; now it must say plainly that S2 is not why they are stuck."""
    with pytest.raises(MissingAPIKeyError) as exc:
        _settings()
    message = str(exc.value)
    assert "SEMANTIC_SCHOLAR_API_KEY is NOT in this list" in message
    assert "MISSING  SEMANTIC_SCHOLAR_API_KEY" not in message


def test_missing_required_keys_reports_without_raising() -> None:
    assert missing_required_keys({}) == list(REQUIRED_KEYS)
    assert missing_required_keys({**ALL_KEYS}) == []
    assert missing_required_keys({**ALL_KEYS, "OPENALEX_API_KEY": " "}) == ["OPENALEX_API_KEY"]


def test_openai_key_is_required_too() -> None:
    """ADR-015: every LLM role depends on it, so it is startup-fatal like the others."""
    env = {k.lower(): v for k, v in ALL_KEYS.items()}
    env["openai_api_key"] = ""
    with pytest.raises(MissingAPIKeyError, match="OPENAI_API_KEY"):
        _settings(**env)


def test_every_role_resolves_to_a_pinned_model() -> None:
    """ADR-015: models are chosen per role, and no model string lives outside config."""
    from app.core.contracts import LLMRole

    s = _settings(**{k.lower(): v for k, v in ALL_KEYS.items()})
    resolved = {role: s.model_for(role) for role in LLMRole}
    assert len(resolved) == 6
    assert all(model for model in resolved.values())
    # Verification is the judgment the product's honesty rests on; it must not be the
    # cheap model (ADR-015 is explicit about this one).
    assert s.model_for(LLMRole.VERIFY) != s.model_for(LLMRole.REPAIR)


def test_all_adr_024_thresholds_are_present_at_their_stated_values() -> None:
    s = _settings(**{k.lower(): v for k, v in ALL_KEYS.items()})
    assert (s.arbiter_accept, s.repair_trigger) == (0.85, 0.75)
    assert (s.reattach_accept, s.reattach_flag_floor) == (0.72, 0.55)
    assert (s.rerank_keep, s.verify_keep) == (10, 3)
    assert (s.citability_min, s.style_ambiguous_delta) == (0.3, 0.05)
    assert s.doc_token_budget == 2_000_000


def test_an_inverted_reattachment_band_is_rejected() -> None:
    """A flag floor above the accept threshold would erase ADR-013's user decision."""
    env = {k.lower(): v for k, v in ALL_KEYS.items()}
    with pytest.raises(ValueError, match="no band"):
        _settings(**env, reattach_accept=0.5, reattach_flag_floor=0.9)


def test_get_settings_raises_when_env_is_unset(monkeypatch) -> None:
    """The failure happens at startup, not on the first request."""
    monkeypatch.setattr("app.core.config.Settings", lambda: _settings())
    reset_settings_cache()
    with pytest.raises(MissingAPIKeyError):
        get_settings()


def test_thresholds_match_the_adrs() -> None:
    """ADR-001 fixes 0.85; ADR-011 fixes 0.05. Drift here changes what 'resolved' means."""
    s = _settings(**{k.lower(): v for k, v in BOTH_KEYS.items()})
    assert s.arbiter_accept == 0.85
    assert s.style_ambiguous_delta == 0.05
