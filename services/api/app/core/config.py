"""Application configuration. HR-2 / ADR-010 / ADR-015 / ADR-024 are enforced here.

Three things live in this module and nowhere else.

**The required credentials.** `SEMANTIC_SCHOLAR_API_KEY`, `OPENALEX_API_KEY` and
`OPENAI_API_KEY` are all required; if any is absent or empty, `get_settings()` raises
`MissingAPIKeyError` and the application does not start. Read ADR-010 before adding a
fallback. The short version: under anonymous limits these APIs do not error, they return
*thin or empty results*, which the review pipeline would faithfully report as "no missing
work found". A false negative dressed as a clean bill of health is worse than a crash,
because it is invisible.

**Every model ID (ADR-015).** Models are chosen per role, never globally. No model string
appears anywhere else in the codebase — if you are about to type `"gpt-5.4"` in a module,
you want `settings.model_for(LLMRole.…)`.

**Every threshold (ADR-024).** These are hypotheses to be tuned against the golden set,
not constants. A threshold scattered as a literal cannot be swept, cannot be tested at
its boundary, and cannot be reported.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.contracts import LLMRole, MissingAPIKeyError

# Repository root, derived from this file: app/core/config.py -> services/api/app/core
_REPO_ROOT = Path(__file__).resolve().parents[4]

LLMMode = Literal["live", "record", "replay"]

# Every required credential, with the place a human actually obtains it. This message is
# the only thing a misconfigured operator will see, so it has to be complete.
REQUIRED_KEYS: dict[str, str] = {
    "SEMANTIC_SCHOLAR_API_KEY": "free — request at https://www.semanticscholar.org/product/api",
    "OPENALEX_API_KEY": "free — register at https://openalex.org (keys became mandatory 2026-02-13)",
    "OPENAI_API_KEY": "https://platform.openai.com/api-keys (ADR-015 — every LLM role uses it)",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- required credentials (HR-2 / ADR-010 / ADR-015) ----------
    semantic_scholar_api_key: str = ""
    openalex_api_key: str = ""
    openai_api_key: str = ""

    # ---------- polite-pool identity ----------
    # OpenAlex asks for a contact address on every call. Not a credential, but traffic
    # without it leaves the polite pool, which looks like a rate limit and therefore
    # like an empty result.
    openalex_mailto: str = ""

    # ---------- services ----------
    grobid_url: str = "http://grobid:8070"
    database_url: str = "postgresql+asyncpg://answerthat:answerthat@postgres:5432/answerthat"
    redis_url: str = "redis://redis:6379/0"

    # ---------- paths ----------
    # Mounted into the api container and read by the frontend's citation.js. One copy,
    # or preview and export drift. HR-4.
    csl_styles_dir: Path = _REPO_ROOT / "packages" / "csl-styles"
    pandoc_bin: str = "pandoc"
    # ADR-022: uploads on a local volume, addressed by doc_id.
    upload_dir: Path = Path("/data/uploads")
    # ADR-018: where record/replay keeps LLM recordings. Committed, so CI runs offline.
    llm_recordings_dir: Path = _REPO_ROOT / "services" / "api" / "tests" / "recordings" / "llm"

    # ---------- GROBID client behaviour ----------
    # The image needs 30-60s to become healthy on first boot. Early connection refusals
    # are expected, not failures.
    grobid_startup_timeout_s: float = 180.0
    grobid_request_timeout_s: float = 300.0

    # ---------- LLM: models per role (ADR-015) ----------
    # Pinned here and nowhere else. The comments are the *why* from ADR-015's table;
    # changing one of these is a decision, so it should be visible in a diff.
    model_repair: str = "gpt-5.4-mini"            # mechanical segment-and-label, high volume
    model_claim_extraction: str = "gpt-5.4"       # structured decomposition over long sections
    model_rerank: str = "gpt-5.4-mini"            # highest call volume, after an embedding prefilter
    model_verify: str = "gpt-5.5"                 # accuracy-critical — do not economise here
    model_plan: str = "gpt-5.5"                   # low volume, high consequence
    model_transform: str = "gpt-5.4"              # rewriting the researcher's own prose
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 512               # ADR-016

    llm_mode: LLMMode = "live"
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 3

    # ---------- thresholds (ADR-024) ----------
    # Hypotheses, not constants. T1 sweeps several of these against the golden set.
    arbiter_accept: float = Field(default=0.85, ge=0.0, le=1.0)
    repair_trigger: float = Field(default=0.75, ge=0.0, le=1.0)
    reattach_accept: float = Field(default=0.72, ge=0.0, le=1.0)
    reattach_flag_floor: float = Field(default=0.55, ge=0.0, le=1.0)
    rerank_keep: int = Field(default=10, ge=1)
    verify_keep: int = Field(default=3, ge=1)
    citability_min: float = Field(default=0.3, ge=0.0, le=1.0)
    style_ambiguous_delta: float = Field(default=0.05, ge=0.0, le=1.0)
    # Per-document LLM ceiling. Exceeding it raises and surfaces rather than silently
    # truncating the review (ADR-015).
    doc_token_budget: int = Field(default=2_000_000, ge=1)

    def model_for(self, role: LLMRole) -> str:
        """The model ID for a role. The only sanctioned way to name a model."""
        return {
            LLMRole.REPAIR: self.model_repair,
            LLMRole.CLAIM_EXTRACTION: self.model_claim_extraction,
            LLMRole.RERANK: self.model_rerank,
            LLMRole.VERIFY: self.model_verify,
            LLMRole.PLAN: self.model_plan,
            LLMRole.TRANSFORM: self.model_transform,
        }[role]

    @model_validator(mode="after")
    def _require_credentials(self) -> Settings:
        """HR-2. Abort startup, naming every missing key and where to get it."""
        present = {
            "SEMANTIC_SCHOLAR_API_KEY": self.semantic_scholar_api_key,
            "OPENALEX_API_KEY": self.openalex_api_key,
            "OPENAI_API_KEY": self.openai_api_key,
        }
        missing = [name for name, value in present.items() if not value.strip()]
        if missing:
            raise MissingAPIKeyError(_missing_key_message(missing))
        return self

    @model_validator(mode="after")
    def _check_reattachment_band(self) -> Settings:
        """A flag floor above the accept threshold would make the FLAG band empty."""
        if self.reattach_flag_floor > self.reattach_accept:
            raise ValueError(
                f"REATTACH_FLAG_FLOOR ({self.reattach_flag_floor}) is above REATTACH_ACCEPT "
                f"({self.reattach_accept}), which leaves no band in which an anchor is "
                "reattached-but-flagged. Every uncertain reattachment would be either silent "
                "or refused, and ADR-013's user decision would never fire."
            )
        return self


def _missing_key_message(missing: list[str]) -> str:
    lines = ["", "Startup aborted: required API key(s) missing or empty.", ""]
    for name in missing:
        lines.append(f"  MISSING  {name}")
        lines.append(f"           {REQUIRED_KEYS[name]}")
    lines += [
        "",
        "Fix it:",
        "  cp .env.example .env      # then set the key(s) above",
        "",
        "This is deliberate and cannot be bypassed (HR-2 / ADR-010 / ADR-015). There is",
        "no anonymous mode. Without a key these APIs do not fail loudly — they return",
        "thin or empty results, which this system would report to a researcher as",
        '"no missing work found". A silent false negative is worse than this crash.',
        "",
    ]
    return "\n".join(lines)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once.

    Raises `MissingAPIKeyError` if any required key is absent or empty. Call this at
    startup so the failure happens before the first request, not during one.
    """
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached settings. Tests only — production reads config once."""
    get_settings.cache_clear()


def missing_required_keys(env: dict[str, str] | None = None) -> list[str]:
    """Names of required keys absent or empty in `env` (default: os.environ).

    A read-only check for healthchecks and startup diagnostics. It does not construct
    `Settings` and therefore does not raise — use `get_settings()` for the hard failure.
    """
    source = os.environ if env is None else env
    return [name for name in REQUIRED_KEYS if not (source.get(name) or "").strip()]
