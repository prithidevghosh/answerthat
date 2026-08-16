"""The model half of the repair tier. ADR-003 / ADR-015 / ADR-019.

`repair.py` holds the guarantee — the mechanical substring check that no prompt can talk
past. This module holds the thing the guarantee is pointed at: one call to
`LLMClient.complete()` with role `REPAIR`, a JSON Schema, and a prompt loaded from
`app/parsing/prompts/`.

They are separate files on purpose. The check must be readable, and testable, without
any of the model plumbing in view — its whole claim is that it does not care what the
model was asked or what it intended.

Three things this module deliberately does not do:

* **It does not name a model.** `settings.model_for(LLMRole.REPAIR)` inside
  `app/core/llm.py` is the only place that happens (ADR-015).
* **It does not hold prompt text.** The prompt is a versioned file (ADR-019).
* **It does not decide whether a reference is trustworthy.** It shapes the model's
  answer into CSL and hands it to `check_substring_containment`, which is what actually
  decides.

The schema is the second line of defence and worth reading as one. Every field is
nullable and `null` is the expected answer for most of them, because a schema that
*requires* a DOI is a schema that instructs the model to produce one.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.core.contracts import LLMRole
from app.parsing.prompts import load_prompt

__all__ = [
    "REFERENCE_SEGMENT_SCHEMA",
    "SegmentingLLM",
    "LLMReferenceSegmenter",
    "build_default_segmenter",
    "csl_from_segmentation",
]

_SYSTEM_PROMPT_FILE = "reference_repair.system.md"
_USER_PROMPT_FILE = "reference_repair.user.md"

# OpenAI strict structured output requires every property to be listed in `required`
# and `additionalProperties: false` at every level. "Optional" is therefore expressed as
# a nullable type, not as an absent key — which suits us: it makes `null` an answer the
# model has to actively give rather than a field it can quietly omit.
_NULLABLE_STRING = {"type": ["string", "null"]}

REFERENCE_SEGMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title", "container_title", "author", "year",
        "page", "volume", "issue", "publisher", "doi", "type",
    ],
    "properties": {
        "title": _NULLABLE_STRING,
        "container_title": _NULLABLE_STRING,
        "author": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["family", "given", "non_dropping_particle"],
                "properties": {
                    "family": _NULLABLE_STRING,
                    "given": _NULLABLE_STRING,
                    "non_dropping_particle": _NULLABLE_STRING,
                },
            },
        },
        "year": {"type": ["integer", "null"]},
        "page": _NULLABLE_STRING,
        "volume": _NULLABLE_STRING,
        "issue": _NULLABLE_STRING,
        "publisher": _NULLABLE_STRING,
        "doi": _NULLABLE_STRING,
        "type": {
            "type": "string",
            "enum": [
                "article-journal", "paper-conference", "chapter", "book", "thesis",
                "report", "webpage", "document", "manuscript", "article",
            ],
        },
    },
}


class SegmentingLLM(Protocol):
    """The slice of Appendix A's `LLMClient` this module uses."""

    async def complete(
        self, role: LLMRole, prompt: str, schema: dict, *, system: str | None = None
    ) -> dict: ...


def csl_from_segmentation(emitted: dict[str, Any]) -> dict[str, Any]:
    """Reshape the schema's flat answer into CSL-JSON.

    Null and empty values are dropped rather than stored. An empty string would pass the
    substring check trivially (every string contains ""), so carrying one forward would
    mean a field that reads as populated and asserts nothing.
    """
    csl: dict[str, Any] = {}

    for emitted_key, csl_key in (
        ("title", "title"),
        ("container_title", "container-title"),
        ("page", "page"),
        ("volume", "volume"),
        ("issue", "issue"),
        ("publisher", "publisher"),
        ("doi", "DOI"),
    ):
        value = emitted.get(emitted_key)
        if isinstance(value, str) and value.strip():
            csl[csl_key] = value.strip()

    authors: list[dict[str, str]] = []
    for entry in emitted.get("author") or []:
        if not isinstance(entry, dict):
            continue
        name: dict[str, str] = {}
        for emitted_key, csl_key in (
            ("family", "family"),
            ("given", "given"),
            ("non_dropping_particle", "non-dropping-particle"),
        ):
            value = entry.get(emitted_key)
            if isinstance(value, str) and value.strip():
                name[csl_key] = value.strip()
        if name:
            authors.append(name)
    if authors:
        csl["author"] = authors

    year = emitted.get("year")
    if isinstance(year, int):
        csl["issued"] = {"date-parts": [[year]]}

    csl_type = emitted.get("type")
    if isinstance(csl_type, str) and csl_type.strip():
        csl["type"] = csl_type.strip()

    return csl


class LLMReferenceSegmenter:
    """Implements `repair.ReferenceSegmenter` over the shared LLM client.

    One call per low-confidence reference. There is no batching here and that is a
    choice, not an omission: a batched call would let a mis-segmentation of entry 12
    contaminate entry 13, and the repair tier only ever runs on the minority of entries
    GROBID already flagged.
    """

    def __init__(self, client: SegmentingLLM) -> None:
        self._client = client
        # Read at construction so a missing prompt file fails when the pipeline is
        # built, not on the first low-confidence reference of the first real upload.
        self._system = load_prompt(_SYSTEM_PROMPT_FILE)
        self._template = load_prompt(_USER_PROMPT_FILE)

    async def segment(self, raw_string: str) -> dict[str, Any]:
        emitted = await self._client.complete(
            LLMRole.REPAIR,
            self._template.format(raw_reference=raw_string),
            REFERENCE_SEGMENT_SCHEMA,
            system=self._system,
        )
        return csl_from_segmentation(emitted)


def build_default_segmenter(client: SegmentingLLM | None = None) -> LLMReferenceSegmenter:
    """The production segmenter, on the process-wide LLM client.

    Imported lazily so that constructing a pipeline in a test — or in `LLM_MODE=replay`
    without the `openai` package installed — does not pull the SDK in.
    """
    if client is None:
        from app.core.llm import get_llm_client  # noqa: PLC0415 - keeps the SDK optional

        client = get_llm_client()
    return LLMReferenceSegmenter(client)
