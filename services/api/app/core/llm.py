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

**Multi-turn tool calling lives here too, not beside it (ADR-031).** `converse()` is the
agentic counterpart of `complete()`: a message list, native tool schemas, and streamed
text deltas. It is in this client rather than in `app/orchestrator/` because a second
OpenAI call site would lose per-role routing, the token budget and record/replay in one
move — the four properties above are properties of *this module*, not of the SDK.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Awaitable, Callable
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
    "ToolCall",
    "AssistantTurn",
    "OpenAILLMClient",
    "get_llm_client",
    "reset_llm_client",
    "recording_key",
    "conversation_key",
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


def conversation_key(
    role: LLMRole, model: str, messages: list[dict], tools: list[dict] | None, system: str | None
) -> str:
    """Stable hash of a `converse()` request.

    Covers the **whole message list and the tool schemas**, not just the last message.
    Anything less would replay one recording for two different conversations that happen
    to end the same way, which is the agentic version of replaying a stale answer — and
    it would do it silently, because the response would still be well-formed.
    """
    payload = json.dumps(
        {
            "role": role.value,
            "model": model,
            "system": system or "",
            "messages": messages,
            "tools": tools or [],
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


@dataclass
class ToolCall:
    """One tool invocation the model asked for.

    `arguments` is already JSON-parsed. Malformed JSON raises `StructuredOutputError`
    rather than arriving as `{}`: an empty argument object is a valid call for several
    tools here, so a parse failure that degraded to one would run the wrong operation on
    the user's paper and look like the model's intent.
    """

    call_id: str
    name: str
    arguments: dict


@dataclass
class AssistantTurn:
    """What one `converse()` call produced."""

    text: str
    """May be empty: a turn that only called tools says nothing to the user yet."""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    tokens: int = 0


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

    async def converse(
        self,
        role: LLMRole,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        system: str | None = None,
        doc_id: str = "",
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> AssistantTurn:
        """One multi-turn, tool-calling round trip. ADR-031.

        `messages` is a list of OpenAI message dicts, `role="tool"` results included.
        `on_text` receives text deltas as they arrive; supplying it switches the request
        to `stream=True`. The user has to see the agent typing — a six-minute review with
        a frozen chat is the failure ADR-014 exists to prevent, one level up.

        Every guarantee `complete()` makes holds here unchanged: per-role model routing,
        record/replay that raises on a miss, and a token budget that raises rather than
        truncating.
        """
        model = self.settings.model_for(role)
        key = conversation_key(role, model, messages, tools, system)
        self.calls.append({"role": role.value, "model": model, "key": key, "kind": "converse"})

        if self.mode == "replay":
            recorded = self.recorder.load(key)
            if recorded is None:
                raise LLMRecordingMissing(
                    f"no recording for role={role.value} model={model} key={key}.\n"
                    f"  expected at: {self.recorder.path_for(key)}\n"
                    "LLM_MODE=replay does not fall through to the network (ADR-018). Re-record "
                    "deliberately with LLM_MODE=record, then commit the new file."
                )
            turn = _turn_from_record(recorded["response"])
            # Replayed text is still delivered through `on_text`, so a client watching the
            # stream sees the same shape it would live. Emitted whole: the chunk
            # boundaries were never part of the recording (see `_record_turn`).
            if on_text is not None and turn.text:
                await on_text(turn.text)
            return turn

        turn = await (
            self._stream_openai(model, messages, tools, system, on_text)
            if on_text is not None
            else self._converse_openai(model, messages, tools, system)
        )

        # Charged after the call, exactly as `complete()` does, and allowed to raise
        # through the caller. The orchestrator turns `TokenBudgetExceeded` into a visible
        # chat error; what it must never become is a conversation that quietly stops.
        if doc_id:
            self.budget.charge(doc_id, turn.tokens)

        if self.mode == "record":
            self.recorder.save(
                key,
                {
                    "role": role.value,
                    "model": model,
                    "system": system,
                    "messages": messages,
                    "tools": tools,
                    "response": _record_turn(turn),
                    "tokens": turn.tokens,
                },
            )
        return turn

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

    # -- conversation internals --------------------------------------------

    def _request_messages(self, messages: list[dict], system: str | None) -> list[dict]:
        return ([{"role": "system", "content": system}] if system else []) + list(messages)

    async def _converse_openai(
        self, model: str, messages: list[dict], tools: list[dict] | None, system: str | None
    ) -> AssistantTurn:
        """Non-streaming path. Used when no `on_text` was supplied."""
        client = self._openai()
        response = await client.chat.completions.create(
            model=model,
            messages=self._request_messages(messages, system),
            **({"tools": tools, "tool_choice": "auto"} if tools else {}),
        )
        choice = response.choices[0]
        finish_reason = str(getattr(choice, "finish_reason", "") or "stop")
        _refuse_on_length(model, finish_reason)

        message = choice.message
        calls = [
            _tool_call_from(
                getattr(call, "id", "") or "",
                getattr(getattr(call, "function", None), "name", "") or "",
                getattr(getattr(call, "function", None), "arguments", "") or "",
                model,
            )
            for call in (getattr(message, "tool_calls", None) or [])
        ]
        usage = getattr(response, "usage", None)
        return AssistantTurn(
            text=getattr(message, "content", None) or "",
            tool_calls=calls,
            finish_reason=finish_reason,
            tokens=int(getattr(usage, "total_tokens", 0) or 0),
        )

    async def _stream_openai(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        system: str | None,
        on_text: Callable[[str], Awaitable[None]],
    ) -> AssistantTurn:
        """Streaming path: text deltas out as they arrive, tool calls reassembled.

        **Tool call fragments are assembled by index, not by chunk.** OpenAI splits a
        single call's `arguments` across many deltas and identifies each only by its
        position in the list; treating one chunk as one call produces a call whose
        arguments are the first eight characters of a JSON object. The id and the name
        arrive once, usually on the first fragment, so both are accumulated rather than
        overwritten with the empty strings that follow.
        """
        client = self._openai()
        stream = await client.chat.completions.create(
            model=model,
            messages=self._request_messages(messages, system),
            stream=True,
            stream_options={"include_usage": True},
            **({"tools": tools, "tool_choice": "auto"} if tools else {}),
        )

        text_parts: list[str] = []
        fragments: dict[int, dict[str, str]] = {}
        finish_reason = ""
        tokens = 0

        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                tokens = int(getattr(usage, "total_tokens", 0) or 0) or tokens
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            finish_reason = str(getattr(choice, "finish_reason", "") or finish_reason)

            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            piece = getattr(delta, "content", None)
            if piece:
                text_parts.append(piece)
                # Awaited inline rather than fired off as a task: the caller's queue is
                # what preserves delta order, and a task per token would reorder them.
                await on_text(piece)

            for call_delta in getattr(delta, "tool_calls", None) or []:
                index = int(getattr(call_delta, "index", 0) or 0)
                slot = fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
                call_id = getattr(call_delta, "id", None)
                if call_id:
                    slot["id"] = call_id
                function = getattr(call_delta, "function", None)
                name = getattr(function, "name", None) if function else None
                if name:
                    slot["name"] = name
                arguments = getattr(function, "arguments", None) if function else None
                if arguments:
                    slot["arguments"] += arguments

        _refuse_on_length(model, finish_reason or "stop")
        calls = [
            _tool_call_from(
                fragments[index]["id"],
                fragments[index]["name"],
                fragments[index]["arguments"],
                model,
            )
            for index in sorted(fragments)
        ]
        return AssistantTurn(
            text="".join(text_parts),
            tool_calls=calls,
            finish_reason=finish_reason or "stop",
            tokens=tokens,
        )


def _refuse_on_length(model: str, finish_reason: str) -> None:
    """`length` is a refusal, exactly as in `_call_openai`.

    A truncated turn is worse here than in a structured call: the cut may land mid
    tool-call arguments, producing a syntactically valid object with a missing field, and
    the agent would act on it.
    """
    if finish_reason == "length":
        raise StructuredOutputError(
            f"{model} hit the output length limit, so this turn is truncated and cannot be "
            "trusted — including any tool call it appears to have made."
        )


def _tool_call_from(call_id: str, name: str, arguments: str, model: str) -> ToolCall:
    try:
        parsed = json.loads(arguments) if arguments.strip() else {}
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            f"{model} produced tool call {name or '<unnamed>'} with arguments that are not "
            f"valid JSON: {arguments[:200]!r}. Nothing is substituted — an empty argument "
            "object is a legal call for several tools, so a default here would run the wrong "
            "operation and look intentional."
        ) from exc
    if not isinstance(parsed, dict):
        raise StructuredOutputError(
            f"{model} produced tool call {name or '<unnamed>'} whose arguments parsed to "
            f"{type(parsed).__name__}, not an object."
        )
    return ToolCall(call_id=call_id, name=name, arguments=parsed)


def _record_turn(turn: AssistantTurn) -> dict:
    """The assembled turn, never the raw chunks.

    Chunk boundaries are a property of one network session, not of the answer. Recording
    them would make an identical response fail to compare equal across re-records and
    would tie the fixture to the SDK's framing.
    """
    return {
        "text": turn.text,
        "tool_calls": [
            {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}
            for call in turn.tool_calls
        ],
        "finish_reason": turn.finish_reason,
        "tokens": turn.tokens,
    }


def _turn_from_record(payload: dict) -> AssistantTurn:
    return AssistantTurn(
        text=payload.get("text", "") or "",
        tool_calls=[
            ToolCall(
                call_id=call.get("call_id", ""),
                name=call.get("name", ""),
                arguments=call.get("arguments") or {},
            )
            for call in payload.get("tool_calls") or []
        ],
        finish_reason=payload.get("finish_reason", "stop"),
        tokens=int(payload.get("tokens", 0) or 0),
    )


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
