"""Application configuration. HR-2 / ADR-010 / ADR-015 / ADR-024 are enforced here.

Three things live in this module and nowhere else.

**The required credentials.** `OPENALEX_API_KEY` and `OPENAI_API_KEY` are required; if
either is absent or empty, `get_settings()` raises `MissingAPIKeyError` and the
application does not start. Read ADR-010 before adding a fallback. The short version:
under anonymous limits OpenAlex does not error, it returns *thin or empty results*, which
the review pipeline would faithfully report as "no missing work found". A false negative
dressed as a clean bill of health is worse than a crash, because it is invisible.

`SEMANTIC_SCHOLAR_API_KEY` is **optional** (ADR-010a). It was required on the same
reasoning until that reasoning was checked against the API: S2 answers a throttled
anonymous call with HTTP 429, which `ProviderHTTP` raises as `ProviderRateLimited` rather
than degrading to `[]`. The false negative ADR-010 guards against cannot occur there, so
the gate only cost us operators — S2 has issued no new keys to free-domain addresses or
third-party apps since 2024-09. Set it if you have one and calls get a dedicated 1 RPS
allowance instead of a shared pool. Do not generalise this to OpenAlex.

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
    "OPENALEX_API_KEY": "free — register at https://openalex.org (keys became mandatory 2026-02-13)",
    "OPENAI_API_KEY": "https://platform.openai.com/api-keys (ADR-015 — every LLM role uses it)",
}

# Credentials that improve service but whose absence cannot cause a silent false negative
# (ADR-010a). Absence is reported at startup, never fatal.
OPTIONAL_KEYS: dict[str, str] = {
    "SEMANTIC_SCHOLAR_API_KEY": (
        "optional — without it S2 calls use the shared unauthenticated pool. Throttling "
        "there is an HTTP 429 we raise on, not a thin result, so no review can silently "
        "under-report. Set it for a dedicated ~1 RPS if you have one; S2 has not issued "
        "new keys to free-domain addresses or third-party apps since 2024-09."
    ),
}


# Where the web app runs under `docker compose up` and `pnpm dev`. Used only when
# CORS_ORIGINS is unset, so a deployment that sets it gets exactly what it asked for.
DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- credentials (HR-2 / ADR-010 / ADR-010a / ADR-015) ----------
    # Optional — see ADR-010a. Empty means "call S2 unauthenticated", which is a supported
    # configuration, not a degraded one.
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

    # ---------- CORS ----------
    # The browser talks to this API directly rather than through a Next.js route handler,
    # including for the review SSE stream (memory.md §5), so *every* origin the web app is
    # served from has to be named here or the stream never opens. Comma-separated, because
    # a deployment sets it as a plain environment variable and pydantic-settings would
    # otherwise demand JSON. Empty falls back to the localhost pair in `allowed_origins()`.
    cors_origins: str = ""
    # For hosts whose origin is not knowable ahead of time — Vercel gives every preview
    # deployment its own subdomain. A regex here rather than a wildcard in `cors_origins`:
    # `allow_origins=["*"]` and `allow_credentials=True` is a combination Starlette
    # silently drops the credentials for, which would look like an auth bug much later.
    cors_origin_regex: str = ""

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
    # ADR-031. Same reasoning as `model_plan`: low volume, high consequence. The
    # orchestrator makes one call per turn, and each one decides whether to commit an
    # edit, spend six minutes of provider budget on a review, or hand the user a file.
    # A cheaper model here saves very little and misroutes the expensive decisions.
    model_orchestrate: str = "gpt-5.5"            # tool-call routing over a conversation
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

    # ---------- orchestrator (ADR-031 / ADR-032 / ADR-033 / ADR-034) ----------
    # Every one of these is a limit, so by ADR-024 it lives here and nowhere else. The
    # agent loop, the watcher and the evidence index all read them from settings.
    #
    # How many model turns one user message may consume before the runtime stops and
    # says so. High enough for "read the report, list the findings, fetch two sources,
    # answer" in one turn; low enough that a model stuck in a retry loop costs bounded
    # money and stops visibly rather than silently.
    orchestrator_max_iterations: int = Field(default=12, ge=1)
    # Approximate token ceiling for the conversation handed to the model. When the
    # history approaches it, the runtime drops *old tool results* — never a user message
    # and never the system prompt — and logs what it dropped (ADR-032).
    orchestrator_context_budget_tokens: int = Field(default=120_000, ge=1_000)
    # Characters per token, for the cheap estimate the trimmer runs on. Deliberately not
    # a tokenizer: a wrong-by-15% estimate that costs nothing beats a per-turn tokenizer
    # dependency, and the budget above is set with the slack to absorb it.
    orchestrator_chars_per_token: int = Field(default=4, ge=1)
    # How often the watcher polls ingest progress while a parse is running. This drives
    # UI `progress` events only — never an agent turn (ADR-033).
    orchestrator_watch_interval_s: float = Field(default=1.0, gt=0.0)
    # Default `k` for `search_evidence`, and the ceiling a model may ask for.
    orchestrator_search_k: int = Field(default=8, ge=1)
    orchestrator_search_k_max: int = Field(default=25, ge=1)
    # How many findings/claims/references one list call returns by default, and at most.
    # A tool that returns forty references in one result burns context the conversation
    # needs later; the model pages instead.
    orchestrator_page_size: int = Field(default=20, ge=1)
    orchestrator_page_size_max: int = Field(default=100, ge=1)
    # Characters of span text embedded per row by the evidence index. Spans are
    # sentences (ADR-026), so this is a guard against a pathological block, not a
    # routine truncation.
    orchestrator_index_text_chars: int = Field(default=2_000, ge=100)
    # Rows embedded per `embed()` call while the index builds.
    orchestrator_index_batch: int = Field(default=64, ge=1)

    def allowed_origins(self) -> list[str]:
        """Origins the browser may call this API from, in CORSMiddleware's order.

        The local pair is the fallback rather than an addition: a deployment that names
        its real origins should not keep an implicit hole for anything on localhost.
        """
        named = [origin.strip().rstrip("/") for origin in self.cors_origins.split(",")]
        return [origin for origin in named if origin] or list(DEFAULT_CORS_ORIGINS)

    def model_for(self, role: LLMRole) -> str:
        """The model ID for a role. The only sanctioned way to name a model."""
        return {
            LLMRole.REPAIR: self.model_repair,
            LLMRole.CLAIM_EXTRACTION: self.model_claim_extraction,
            LLMRole.RERANK: self.model_rerank,
            LLMRole.VERIFY: self.model_verify,
            LLMRole.PLAN: self.model_plan,
            LLMRole.TRANSFORM: self.model_transform,
            LLMRole.ORCHESTRATE: self.model_orchestrate,
        }[role]

    @model_validator(mode="after")
    def _require_credentials(self) -> Settings:
        """HR-2. Abort startup, naming every missing key and where to get it.

        `SEMANTIC_SCHOLAR_API_KEY` is deliberately absent from this check (ADR-010a); it
        is reported by `unauthenticated_providers()`, not enforced.
        """
        present = {
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
        "This is deliberate and cannot be bypassed (HR-2 / ADR-010 / ADR-015). Without a",
        "key these APIs do not fail loudly — they return thin or empty results, which this",
        'system would report to a researcher as "no missing work found". A silent false',
        "negative is worse than this crash.",
        "",
        "SEMANTIC_SCHOLAR_API_KEY is NOT in this list and is not needed to start — S2",
        "throttles with an HTTP 429 we raise on, so it cannot fail silently (ADR-010a).",
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


def unauthenticated_providers(env: dict[str, str] | None = None) -> list[str]:
    """Names of optional keys absent or empty in `env` (default: os.environ).

    Never fatal. It exists so that startup and `/health` can *say* which providers are
    running unauthenticated — HR-3 applies to configuration as much as to results, and
    "we are in the shared S2 pool" should be visible rather than inferred from latency.
    """
    source = os.environ if env is None else env
    return [name for name in OPTIONAL_KEYS if not (source.get(name) or "").strip()]
