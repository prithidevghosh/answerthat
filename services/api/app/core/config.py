"""Application configuration. HR-2 / ADR-010 is enforced here.

`SEMANTIC_SCHOLAR_API_KEY` and `OPENALEX_API_KEY` are **required**. If either is absent
or empty, `get_settings()` raises `MissingAPIKeyError` and the application does not
start. There is no anonymous mode, no default, no fallback.

Read ADR-010 before you are tempted to add one. The short version: under anonymous
limits these APIs do not error, they return *thin or empty results*, which the review
pipeline would faithfully report as "no missing work found". A false negative dressed as
a clean bill of health is worse than a crash, because it is invisible.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.contracts import MissingAPIKeyError

# Repository root, derived from this file: app/core/config.py -> services/api/app/core
_REPO_ROOT = Path(__file__).resolve().parents[4]

# Every required credential, with the place a human actually obtains it. The message
# below is the only thing a misconfigured operator will see, so it has to be complete.
REQUIRED_KEYS: dict[str, str] = {
    "SEMANTIC_SCHOLAR_API_KEY": "free — request at https://www.semanticscholar.org/product/api",
    "OPENALEX_API_KEY": "free — register at https://openalex.org (keys became mandatory 2026-02-13)",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- required credentials (HR-2 / ADR-010) ----------
    semantic_scholar_api_key: str = ""
    openalex_api_key: str = ""

    # ---------- polite-pool identity ----------
    # OpenAlex asks for a contact address on every call. Not a credential, but sending
    # traffic without it gets us moved out of the polite pool, which looks like a rate
    # limit and therefore like an empty result. Required for the same reason as above.
    openalex_mailto: str = ""

    # ---------- model access ----------
    # Not covered by HR-2, which names exactly two keys, so its absence does not abort
    # startup. It is validated at the point of use instead: the repair tier, the
    # planner, claim extraction and the verifier each raise `MissingAPIKeyError` rather
    # than quietly skipping their step. See memory.md §4.
    anthropic_api_key: str = ""

    # ---------- services ----------
    grobid_url: str = "http://grobid:8070"
    database_url: str = "postgresql+asyncpg://answerthat:answerthat@postgres:5432/answerthat"
    redis_url: str = "redis://redis:6379/0"

    # ---------- paths ----------
    # Mounted into the api container and read by the frontend's citation.js. One copy,
    # or preview and export drift. HR-4.
    csl_styles_dir: Path = _REPO_ROOT / "packages" / "csl-styles"
    pandoc_bin: str = "pandoc"
    upload_dir: Path = Path("/tmp/answerthat/uploads")

    # ---------- GROBID client behaviour ----------
    # The image needs 30-60s to become healthy on first boot. Early connection refusals
    # are expected, not failures — the client waits this long before giving up.
    grobid_startup_timeout_s: float = 180.0
    grobid_request_timeout_s: float = 300.0

    # ---------- parsing thresholds ----------
    # Below this, the constrained repair tier runs (ADR-003). It never runs above it.
    repair_confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    # The arbiter accepts an external record as canonical at or above this. ADR-001
    # fixes the value at 0.85; changing it changes what "resolved" means, so it needs
    # an ADR, not an env var edit.
    arbiter_accept_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    # Two styles within this distance of each other are reported as ambiguous and the
    # user picks. ADR-011.
    style_ambiguity_margin: float = Field(default=0.05, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _require_credentials(self) -> Settings:
        """HR-2. Abort startup, naming every missing key and where to get it."""
        missing = [
            name
            for name, attr in (
                ("SEMANTIC_SCHOLAR_API_KEY", self.semantic_scholar_api_key),
                ("OPENALEX_API_KEY", self.openalex_api_key),
            )
            if not attr.strip()
        ]
        if missing:
            raise MissingAPIKeyError(_missing_key_message(missing))
        return self


def _missing_key_message(missing: list[str]) -> str:
    lines = [
        "",
        "Startup aborted: required API key(s) missing or empty.",
        "",
    ]
    for name in missing:
        lines.append(f"  MISSING  {name}")
        lines.append(f"           {REQUIRED_KEYS[name]}")
    lines += [
        "",
        "Fix it:",
        "  cp .env.example .env      # then set the key(s) above",
        "",
        "This is deliberate and cannot be bypassed (HR-2 / ADR-010). There is no",
        "anonymous mode. Without a key these APIs do not fail loudly — they return",
        "thin or empty results, which this system would report to a researcher as",
        '"no missing work found". A silent false negative is worse than this crash.',
        "",
    ]
    return "\n".join(lines)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once.

    Raises `MissingAPIKeyError` if either required key is absent or empty. Call this at
    startup so the failure happens before the first request, not during one.
    """
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached settings. Tests only — production reads config once."""
    get_settings.cache_clear()


def missing_required_keys(env: dict[str, str] | None = None) -> list[str]:
    """Names of required keys that are absent or empty in `env` (default: os.environ).

    A read-only check for healthchecks and startup diagnostics. It does not construct
    `Settings` and therefore does not raise — use `get_settings()` when you want the
    hard failure.
    """
    source = os.environ if env is None else env
    return [name for name in REQUIRED_KEYS if not (source.get(name) or "").strip()]
