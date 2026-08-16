"""B3's three model ports, all of them backed by `app/core/llm.py`.

**There is no OpenAI client in this file, and there must never be one.** Per-role model
routing (ADR-015), mandatory JSON-Schema structured output, the per-document token budget
and record/replay (ADR-018) all live in `app/core/llm.py`, and none of them survive a
second call site. A module that constructs its own client bypasses all four and breaks CI,
which runs with zero live calls.

Three adapters on purpose, over the one client:

* the **planner** is given a JSON schema and cannot return prose (role `PLAN`),
* the **text model** is given prose and never sees a citation (role `TRANSFORM`),
* the **embedder** does the fingerprint arithmetic that reattaches anchors (ADR-016).

Keeping them apart is what makes "the text model never sees a citation marker" a checkable
statement about the wiring rather than a claim about a prompt.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.contracts import LLMRole

log = logging.getLogger("app.api.models")

# The rewrite comes back inside a schema rather than as free text. Two things follow:
# the model cannot prepend "Sure, here's the revised paragraph", and a truncated response
# fails to parse instead of arriving as a half-sentence that would silently replace the
# author's prose.
REVISED_TEXT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["revised_text"],
    "properties": {
        "revised_text": {
            "type": "string",
            "description": "the revised prose, with no citation markers of any kind",
        }
    },
}


class LLMStructuredModel:
    """Planner backend, role `PLAN`.

    The schema is passed straight through to `complete()`, so the response is plan-shaped
    or it raises. There is no code path here that accepts free text and tries to parse a
    plan out of it.
    """

    def __init__(self, client: Any, *, role: LLMRole = LLMRole.PLAN) -> None:
        self._client = client
        self._role = role

    async def plan(self, *, system: str, prompt: str, schema: dict) -> dict:
        response = await self._client.complete(self._role, prompt, schema, system=system)
        if not isinstance(response, dict):
            raise RuntimeError(
                f"the planner returned {type(response).__name__} despite a strict JSON schema; "
                f"refusing to interpret it as a plan"
            )
        return response


class LLMTextModel:
    """Prose rewriting, role `TRANSFORM`. Receives marker-free text, returns prose."""

    def __init__(self, client: Any, *, role: LLMRole = LLMRole.TRANSFORM) -> None:
        self._client = client
        self._role = role

    async def rewrite(self, *, system: str, instruction: str, text: str) -> str:
        response = await self._client.complete(
            self._role,
            f"{instruction}\n\n---\n\n{text}",
            REVISED_TEXT_SCHEMA,
            system=system,
        )
        revised = response.get("revised_text")
        if not isinstance(revised, str):
            raise RuntimeError(
                "the text model returned no `revised_text` string. Nothing is substituted for "
                "it: an empty rewrite would look like a successful edit that deleted a paragraph."
            )
        return revised.strip()


class LLMEmbedder:
    """Sentence embeddings for anchor fingerprints, via `LLMClient.embed()` (ADR-016).

    There is deliberately no local fallback embedder here. A cruder embedder does not fail
    visibly — it shifts anchors into the flag and surface bands, and a user faced with
    twenty spurious "where does this citation go?" prompts learns to click through them.
    That is HR-5 being worn down rather than enforced, so a missing key raises instead.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._client.embed(texts)


def build_model_clients(settings: Any) -> tuple[Any, Any, Any]:
    """(embedder, text_model, structured_model), all over the one shared LLM client.

    `OPENAI_API_KEY` is required at startup by `app/core/config.py` (HR-2 / ADR-015), so
    there is no key check here and no offline edit mode to fall back to.
    """
    from app.core.llm import get_llm_client  # noqa: PLC0415

    client = get_llm_client(settings)
    log.info(
        "model clients bound: plan=%s transform=%s embed=%s@%d (LLM_MODE=%s)",
        settings.model_for(LLMRole.PLAN),
        settings.model_for(LLMRole.TRANSFORM),
        settings.embedding_model,
        settings.embedding_dimensions,
        settings.llm_mode,
    )
    return LLMEmbedder(client), LLMTextModel(client), LLMStructuredModel(client)


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = [
    "LLMEmbedder",
    "LLMStructuredModel",
    "LLMTextModel",
    "REVISED_TEXT_SCHEMA",
    "as_json",
    "build_model_clients",
]
