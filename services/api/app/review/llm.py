"""The review pipeline's model access. ADR-015.

**There is no model SDK in this file, and there must never be one.** Every call goes
through `app/core/llm.py`, which is the only place an OpenAI client is constructed. Four
properties live there and none of them survives a second call site:

* **per-role model routing** — this package declares `CLAIM_EXTRACTION`, `RERANK` and
  `VERIFY`, and `config.py` alone maps those to models. No model ID appears in
  `app/review/` at all, not even in a comment; a module that names one has already broken
  ADR-015 whether or not it happens to name the right one.
* **mandatory JSON-Schema structured output** — the verifier cannot answer with an essay.
* **record/replay** (ADR-018) — CI runs with zero live calls, and a cache miss raises.
* **the per-document token budget** — which is why `doc_id` is threaded through every call
  here rather than left for the caller to remember.

The cost control for this pipeline is the **cascade**, not a cheaper verifier. An
embedding prefilter (free, local, `embed()`) cuts candidates to `RERANK_KEEP`; the mini
model behind `RERANK` reranks those; only `VERIFY_KEEP` reach `VERIFY`. Verification is
the judgment the product's honesty rests on and is not the place to economise — see
ADR-015's table for which model each role resolves to and why.

Nothing here retries into a default value. A refusal, a truncation, or a schema failure
propagates as an exception from `app/core/llm.py`, because a review that swallowed one
would report fewer findings and read as a cleaner paper (HR-3).
"""

from __future__ import annotations

from typing import Any

from app.core.contracts import LLMRole

__all__ = ["ReviewLLM", "object_schema"]


class ReviewLLM:
    """Role-routed structured-output calls for claim extraction, rerank and verification.

    A thin adapter over the shared `LLMClient`, not a client of its own. It exists so the
    three review services state their role at the call site — the role is what selects the
    model, and reading `LLMRole.VERIFY` next to the verifier's prompt is how "we did not
    economise on verification" stays true through later edits.
    """

    def __init__(self, client: Any = None, *, settings: Any = None, doc_id: str = "") -> None:
        if client is None:
            from app.core.llm import get_llm_client  # noqa: PLC0415

            client = get_llm_client(settings)
        self._client = client
        #: Charged against the per-document token budget (ADR-015). Empty means the
        #: caller is outside a document context — a test, or the edit path's ad-hoc
        #: verification — and no budget is charged.
        self.doc_id = doc_id
        self.calls_by_role: dict[str, int] = {}

    def for_document(self, doc_id: str) -> ReviewLLM:
        """A view of this client that charges its tokens to `doc_id`.

        Shares the underlying client, so the budget, the recorder and the counters stay
        process-wide; only the attribution changes.
        """
        scoped = ReviewLLM(self._client, doc_id=doc_id)
        scoped.calls_by_role = self.calls_by_role
        return scoped

    async def complete_json(
        self,
        *,
        role: LLMRole,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        doc_id: str | None = None,
    ) -> dict[str, Any]:
        """One structured-output call. Returns the parsed object, never raw text."""
        self.calls_by_role[role.value] = self.calls_by_role.get(role.value, 0) + 1
        payload = await self._client.complete(
            role,
            prompt,
            schema,
            system=system,
            doc_id=self.doc_id if doc_id is None else doc_id,
        )
        if not isinstance(payload, dict):
            # Should be impossible under a strict object schema. If the contract ever
            # breaks we want to know here, not to coerce it into something usable.
            raise TypeError(
                f"role {role.value} returned {type(payload).__name__} despite a strict "
                "JSON schema; refusing to interpret it."
            )
        return payload

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Sentence embeddings at 512 dimensions (ADR-016), for the rerank prefilter."""
        if not texts:
            return []
        return await self._client.embed(texts)

    def snapshot(self) -> dict[str, Any]:
        return {"doc_id": self.doc_id, "calls_by_role": dict(self.calls_by_role)}


def object_schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    """Build a structured-output-compatible object schema.

    Strict structured output requires `additionalProperties: false` on every object and an
    explicit `required` list naming every property; forgetting either is a 400 at request
    time rather than a silently looser schema.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties),
        "additionalProperties": False,
    }
