"""Model clients: the planner, the prose rewriter, and the embedder.

Three separate clients on purpose. The planner is given a JSON schema and cannot return
prose; the text model is given prose and never sees a citation; the embedder does the
fingerprint arithmetic that reattaches anchors. Keeping them apart is what makes the
statement "the text model never sees a citation marker" checkable rather than aspirational.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("app.api.models")

PLANNER_MODEL = "claude-opus-5"
TEXT_MODEL = "claude-sonnet-5"
EMBEDDING_MODEL = "voyage-3"

PLAN_TOOL_NAME = "emit_edit_plan"


class AnthropicStructuredModel:
    """Planner backend. Uses a forced tool call so the response is schema-shaped or absent —
    there is no code path here that accepts free text and tries to parse a plan out of it."""

    def __init__(self, client: Any, model: str = PLANNER_MODEL, max_tokens: int = 4096) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    async def plan(self, *, system: str, prompt: str, schema: dict) -> dict:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=[
                {
                    "name": PLAN_TOOL_NAME,
                    "description": "Emit the edit plan. This is the only way to respond.",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": PLAN_TOOL_NAME},
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == PLAN_TOOL_NAME:
                return dict(block.input)

        raise RuntimeError(
            "the planner returned no tool call despite tool_choice forcing one; refusing to "
            "interpret free text as a plan"
        )


class AnthropicTextModel:
    """Prose rewriting. Receives marker-free text and returns prose."""

    def __init__(self, client: Any, model: str = TEXT_MODEL, max_tokens: int = 4096) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    async def rewrite(self, *, system: str, instruction: str, text: str) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": f"{instruction}\n\n---\n\n{text}"}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()


class VoyageEmbedder:
    """Sentence embeddings for citation fingerprints."""

    def __init__(self, client: Any, model: str = EMBEDDING_MODEL) -> None:
        self._client = client
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result = await self._client.embed(texts=texts, model=self._model, input_type="document")
        return [list(vector) for vector in result.embeddings]


class HashingEmbedder:
    """Deterministic local fallback for the embedder only.

    This is not a degraded mode in the ADR-010 sense: it affects *where a citation is
    proposed to land*, and anything it places poorly lands below threshold and becomes a
    visible user decision rather than a silent error. It is used when no embedding
    provider is configured, and it says so in the logs at startup.
    """

    DIMENSIONS = 256

    def __init__(self) -> None:
        log.warning(
            "no embedding provider configured; using the local hashing embedder. Reattachment "
            "will be cruder and more anchors will fall to a user decision."
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        import re

        vectors = []
        for text in texts:
            vector = [0.0] * self.DIMENSIONS
            for token in re.findall(r"[a-z0-9]+", text.lower()):
                digest = hashlib.sha256(token.encode()).digest()
                vector[int.from_bytes(digest[:2], "big") % self.DIMENSIONS] += 1.0
            vectors.append(vector)
        return vectors


def build_model_clients(settings: Any) -> tuple[Any, Any, Any]:
    """(embedder, text_model, structured_model).

    `ANTHROPIC_API_KEY` is required — the planner and the rewriter are not optional parts
    of this product, and an edit console with no model behind it would be a blank box that
    silently does nothing.
    """
    from anthropic import AsyncAnthropic  # noqa: PLC0415

    api_key = getattr(settings, "anthropic_api_key", None)
    if not api_key:
        from app.core.contracts import MissingAPIKeyError  # noqa: PLC0415

        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is required: the planner and the text model both need it. "
            "There is no offline edit mode."
        )

    client = AsyncAnthropic(api_key=api_key)
    text_model = AnthropicTextModel(client, getattr(settings, "text_model", TEXT_MODEL))
    structured_model = AnthropicStructuredModel(client, getattr(settings, "planner_model", PLANNER_MODEL))

    voyage_key = getattr(settings, "voyage_api_key", None)
    if voyage_key:
        import voyageai  # noqa: PLC0415

        embedder = VoyageEmbedder(voyageai.AsyncClient(api_key=voyage_key))
    else:
        embedder = HashingEmbedder()

    return embedder, text_model, structured_model


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = [
    "AnthropicStructuredModel",
    "AnthropicTextModel",
    "HashingEmbedder",
    "VoyageEmbedder",
    "build_model_clients",
]
