"""The only path to OpenAI. ADR-015 / ADR-016 / ADR-018.

Four properties this module exists to guarantee, none of which survive being reimplemented
per call site:

**Models are chosen per role.** `settings.model_for(role)` is the only place a model ID is
resolved. No model string appears anywhere else in the codebase (ADR-015).

**Structured output is mandatory for every data-returning call.** `complete()` takes a JSON
Schema and returns a parsed object. It is not "prompt and parse" — the planner *cannot*
emit prose by construction (ADR-007), and a schema is how that is true rather than hoped.

**Replay raises on a miss.** With `LLM_MODE=replay`, a request with no recording raises
`LLMRecordingMissing`. It does not fall through to the network. A test suite that quietly
makes live calls is neither reproducible nor honest about what it covered (ADR-018).

**The token budget is enforced, and exceeding it raises.** Not truncates. A review that
silently dropped half a paper's claims would report fewer findings — the same false
negative as ADR-010, arrived at from a different direction (HR-3).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.contracts import LLMRole, MissingAPIKeyError

__all__ = [
    "LLMError",
    "LLMRecordingMissing",
    "TokenBudgetExceeded",
    "StructuredOutputError",
    "TokenBudget",
    "Recorder",
    "OpenAILLMClient",
    "get_llm_client",
    "reset_llm_client",
    "recording_key",
]


class LLMError(RuntimeError):
    """Any failure of the model layer. Never caught to return a default."""


class LLMRecordingMissing(LLMError):
    """LLM_MODE=replay and no recording exists for this request.

    Deliberately fatal. The fix is to re-record intentionally
    (`LLM_MODE=record`), never to let the test reach the network.
    """


class TokenBudgetExceeded(LLMError):
    """The per-document ceiling was reached. ADR-015."""


class StructuredOutputError(LLMError):
    """The model returned something that is not valid JSON for the supplied schema."""


def recording_key(role: LLMRole, model: str, prompt: str, schema: dict, system: str | None) -> str:
    """Stable hash of everything that determines a response.

    Includes the model: a recording made against one model must not be replayed for
    another, or a model change would silently pass CI on stale answers.
    """
    payload = json.dumps(
        {
            "role": role.value,
            "model": model,
            "system": system or "",
            "prompt": prompt,
            "schema": schema,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


@dataclass
class TokenBudget:
    """Per-document LLM ceiling (ADR-015).

    Counted per document because that is the unit a user pays attention to and the unit
    a runaway loop blows through. Exceeding it raises; there is no truncation path.
    """

    limit: int
    used: dict[str, int] = field(default_factory=dict)

    def charge(self, doc_id: str, tokens: int) -> None:
        total = self.used.get(doc_id, 0) + max(0, tokens)
        if total > self.limit:
            raise TokenBudgetExceeded(
                f"document {doc_id!r} would use {total:,} LLM tokens, over its budget of "
                f"{self.limit:,}. Nothing is truncated to fit: a review that quietly dropped "
                "part of the paper would report fewer findings and read as a cleaner paper "
                "(HR-3). Raise DOC_TOKEN_BUDGET deliberately, or narrow the request."
            )
        self.used[doc_id] = total

    def spent(self, doc_id: str) -> int:
        return self.used.get(doc_id, 0)


class Recorder:
    """Reads and writes LLM recordings on disk, one JSON file per request hash."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def load(self, key: str) -> dict | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # A corrupt recording is a broken fixture, not a cache miss. Falling back to
            # the network here would hide it for as long as the network is available.
            raise LLMError(f"recording {path} is not valid JSON: {exc}") from exc

    def save(self, key: str, record: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path_for(key).write_text(
            json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
        )


class OpenAILLMClient:
    """Implements the Appendix A `LLMClient` protocol.

    The OpenAI SDK is imported lazily, so `LLM_MODE=replay` runs — in CI, in a container
    without the package, on a laptop with no key — without ever touching it.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        recorder: Recorder | None = None,
        budget: TokenBudget | None = None,
        client: Any = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.mode = self.settings.llm_mode
        self.recorder = recorder or Recorder(self.settings.llm_recordings_dir)
        self.budget = budget or TokenBudget(limit=self.settings.doc_token_budget)
        self._client = client
        self.calls: list[dict] = []

    # -- protocol ----------------------------------------------------------

    async def complete(
        self,
        role: LLMRole,
        prompt: str,
        schema: dict,
        *,
        system: str | None = None,
        doc_id: str = "",
    ) -> dict:
        """One structured-output call. Returns the parsed object, never raw text."""
        model = self.settings.model_for(role)
        key = recording_key(role, model, prompt, schema, system)
        self.calls.append({"role": role.value, "model": model, "key": key})

        if self.mode == "replay":
            recorded = self.recorder.load(key)
            if recorded is None:
                raise LLMRecordingMissing(
                    f"no recording for role={role.value} model={model} key={key}.\n"
                    f"  expected at: {self.recorder.path_for(key)}\n"
                    "LLM_MODE=replay does not fall through to the network (ADR-018). Re-record "
                    "deliberately with LLM_MODE=record, then commit the new file."
                )
            return recorded["response"]

        response, usage = await self._call_openai(model, prompt, schema, system)
        if doc_id:
            self.budget.charge(doc_id, usage)

        if self.mode == "record":
            self.recorder.save(
                key,
                {
                    "role": role.value,
                    "model": model,
                    "system": system,
                    "prompt": prompt,
                    "schema": schema,
                    "response": response,
                    "tokens": usage,
                },
            )
        return response

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Sentence embeddings at 512 dimensions (ADR-016)."""
        if not texts:
            return []

        key = recording_key(
            LLMRole.RERANK,  # embeddings have no role of their own; key on the model below
            f"{self.settings.embedding_model}@{self.settings.embedding_dimensions}",
            json.dumps(texts, ensure_ascii=False),
            {"kind": "embedding"},
            None,
        )
        if self.mode == "replay":
            recorded = self.recorder.load(key)
            if recorded is None:
                raise LLMRecordingMissing(
                    f"no embedding recording for {len(texts)} text(s), key={key}.\n"
                    f"  expected at: {self.recorder.path_for(key)}\n"
                    "Re-record with LLM_MODE=record (ADR-018)."
                )
            return recorded["response"]

        client = self._openai()
        result = await client.embeddings.create(
            model=self.settings.embedding_model,
            input=texts,
            dimensions=self.settings.embedding_dimensions,
        )
        vectors = [item.embedding for item in result.data]

        if len(vectors) != len(texts):
            raise LLMError(
                f"asked for {len(texts)} embeddings and got {len(vectors)}. Positional "
                "alignment is the caller's only way to match a vector to its sentence, so a "
                "short result cannot be used."
            )
        if self.mode == "record":
            self.recorder.save(key, {"model": self.settings.embedding_model, "response": vectors})
        return vectors

    # -- internals ---------------------------------------------------------

    def _openai(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.settings.openai_api_key.strip():
            raise MissingAPIKeyError(
                "OPENAI_API_KEY is empty, so no LLM call can be made. Every model role — "
                "repair, claim extraction, rerank, verification, planning, transform — depends "
                "on it (ADR-015). This raises rather than skipping the step, because a review "
                "that ran with no verifier reports no findings, which is indistinguishable "
                "from a paper with nothing missing (HR-3)."
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - packaging problem, not logic
            raise LLMError(
                "the `openai` package is not installed, so live and record modes cannot run. "
                "LLM_MODE=replay works without it."
            ) from exc
        self._client = AsyncOpenAI(
            api_key=self.settings.openai_api_key, timeout=self.settings.llm_timeout_s
        )
        return self._client

    async def _call_openai(
        self, model: str, prompt: str, schema: dict, system: str | None
    ) -> tuple[dict, int]:
        client = self._openai()
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema, "strict": True},
            },
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        if getattr(choice, "finish_reason", None) == "length":
            # A truncated JSON body may still parse into something plausible. Refuse it.
            raise StructuredOutputError(
                f"{model} hit the output length limit, so its response is truncated and cannot "
                "be trusted to be the object it appears to be."
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise StructuredOutputError(
                f"{model} returned output that is not valid JSON despite a strict schema: "
                f"{content[:200]!r}"
            ) from exc
        usage = getattr(response, "usage", None)
        return parsed, int(getattr(usage, "total_tokens", 0) or 0)


_CLIENT: OpenAILLMClient | None = None


def get_llm_client(settings: Settings | None = None) -> OpenAILLMClient:
    """The process-wide client. One budget, one recorder, one place models are named."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = OpenAILLMClient(settings or get_settings())
    return _CLIENT


def reset_llm_client() -> None:
    """Drop the cached client. Tests only."""
    global _CLIENT
    _CLIENT = None


def llm_mode_from_env(default: str = "live") -> str:
    """`LLM_MODE` as a plain string, for callers that only need to branch on it."""
    return (os.environ.get("LLM_MODE") or default).strip().lower()
