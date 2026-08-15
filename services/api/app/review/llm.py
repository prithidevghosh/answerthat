"""The single Anthropic call site for the review pipeline.

Three rules, all of them HR-3 in one form or another:

* **The key is required at the point of use.** `ANTHROPIC_API_KEY` is not one of
  HR-2's two startup keys, so its absence does not abort the app — but claim
  extraction, reranking and verification must not silently skip when it is missing.
  They raise `MissingAPIKeyError` instead (see memory.md §4, B1's note).
* **Structured output only.** Every call passes a JSON schema through
  `output_config.format`, so the model cannot answer with prose. A verifier that can
  return an essay is a verifier whose output has to be parsed with a regex.
* **A refusal is raised, not defaulted.** `stop_reason: "refusal"` and truncation both
  raise. Returning an empty verdict here would read downstream as
  "does_not_address" — a confident negative produced by a failure.

Nothing in this module retries into a default value. The Anthropic SDK's own retry
policy handles transient 429/5xx; anything past it propagates.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message
from anthropic.types.output_config_param import OutputConfigParam

from app.core.contracts import MissingAPIKeyError

__all__ = ["StructuredLLM", "LLMRefusal", "LLMTruncated", "DEFAULT_MODEL", "object_schema"]

#: The review pipeline's model. Claim extraction, reranking and verification are all
#: judgement tasks where a wrong answer is a wrong finding shown to a researcher.
DEFAULT_MODEL = "claude-opus-5"


class LLMRefusal(RuntimeError):
    """The model declined the request. Surfaced, never converted into a verdict."""


class LLMTruncated(RuntimeError):
    """The response hit `max_tokens` before the JSON was complete.

    Raised rather than salvaged: half a JSON object parsed leniently is how a
    partial claim list becomes a complete-looking review.
    """


class StructuredLLM:
    """Async Anthropic client constrained to JSON-schema responses."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = DEFAULT_MODEL,
        effort: str = "high",
        client: AsyncAnthropic | None = None,
    ) -> None:
        key = (api_key or "").strip()
        if not key and client is None:
            raise MissingAPIKeyError(
                "\nANTHROPIC_API_KEY is missing or empty.\n"
                "           https://console.anthropic.com/settings/keys\n\n"
                "Claim extraction, reranking and verification all need it. This raises "
                "rather than skipping those steps, because a review that silently ran "
                "without a verifier would report no findings — indistinguishable from a "
                "paper with nothing missing (HR-3).\n"
            )
        self.model = model
        self.effort = effort
        self._client = client or AsyncAnthropic(api_key=key)
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    async def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 8000,
    ) -> dict[str, Any]:
        """Return the model's answer as a dict conforming to `schema`.

        `max_tokens` bounds thinking *and* answer together, so it is set generously —
        a truncated structured response raises rather than returning partial data.
        """
        # Typed as the SDK's param rather than inferred: a bare dict literal widens to
        # `dict[str, Collection[str]]` and stops matching the `create` overloads.
        output_config: OutputConfigParam = {
            "effort": self.effort,  # type: ignore[typeddict-item]
            "format": {"type": "json_schema", "schema": schema},
        }
        response: Message = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config=output_config,
        )

        self.calls += 1
        if response.usage:
            self.input_tokens += response.usage.input_tokens or 0
            self.output_tokens += response.usage.output_tokens or 0

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMRefusal(
                f"the model declined this request (category: "
                f"{getattr(details, 'category', None)}). Surfacing rather than "
                "returning an empty result."
            )
        if response.stop_reason == "max_tokens":
            raise LLMTruncated(
                f"response hit max_tokens ({max_tokens}) before the JSON was complete. "
                "Raising rather than parsing a partial object."
            )

        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise LLMTruncated(
                "the model returned no text content. Raising rather than treating an "
                "empty response as an empty result."
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            # Should be impossible under output_config.format; if the schema contract
            # ever breaks we want to know, not to fall back to a regex.
            raise LLMTruncated(
                f"structured output was not valid JSON despite a schema: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise LLMTruncated(f"structured output was {type(payload).__name__}, not an object")
        return payload

    def snapshot(self) -> dict[str, int | str]:
        return {
            "model": self.model,
            "effort": self.effort,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def object_schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    """Build a structured-output-compatible object schema.

    Structured outputs require `additionalProperties: false` on every object and an
    explicit `required` list; forgetting either is a 400 at request time rather than a
    silently looser schema.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties),
        "additionalProperties": False,
    }
