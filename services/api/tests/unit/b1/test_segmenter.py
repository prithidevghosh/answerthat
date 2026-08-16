"""The model half of the repair tier. ADR-003 / ADR-015 / ADR-019.

`test_repair.py` covers the guarantee — the substring check, adversarially. This file
covers the thing the guarantee is pointed at: that the call is made through the shared
client with role `REPAIR`, that the schema constrains what can come back, and that the
reshaping into CSL does not itself introduce a value the raw string never contained.

The last one matters more than it looks. The substring check runs on the CSL we build,
not on the model's raw answer, so a mapping bug that manufactures a value would slip a
fabrication past the mechanism designed to catch fabrications.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.contracts import ConfidenceTier, LLMRole, ParsedReference
from app.parsing.prompts import PROMPTS_DIR, PromptMissing, load_prompt
from app.parsing.repair import repair_reference
from app.parsing.segmenter import (
    REFERENCE_SEGMENT_SCHEMA,
    LLMReferenceSegmenter,
    csl_from_segmentation,
)

pytestmark = pytest.mark.anyio

THRESHOLD = 0.75

RAW = 'J. van der Berg, "Efficient transformers: a survey," ACM Comput. Surv., vol. 55, no. 6, pp. 1-28, 2022.'


def _emitted(**overrides: Any) -> dict[str, Any]:
    """A schema-shaped answer with every field present, as strict mode requires."""
    base: dict[str, Any] = {
        "title": None,
        "container_title": None,
        "author": [],
        "year": None,
        "page": None,
        "volume": None,
        "issue": None,
        "publisher": None,
        "doi": None,
        "type": "article-journal",
    }
    base.update(overrides)
    return base


class RecordingLLM:
    """Captures the call so the test can assert on the role and the schema."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self, role: LLMRole, prompt: str, schema: dict, *, system: str | None = None
    ) -> dict:
        self.calls.append({"role": role, "prompt": prompt, "schema": schema, "system": system})
        return self.response


# ---------------------------------------------------------------- ADR-019: prompt files


def test_prompts_are_files_on_disk_not_inline_strings() -> None:
    for name in ("reference_repair.system.md", "reference_repair.user.md"):
        assert (PROMPTS_DIR / name).is_file(), f"{name} must be a versioned file (ADR-019)"
        assert load_prompt(name).strip()


def test_a_missing_prompt_raises_rather_than_running_on_no_instruction() -> None:
    with pytest.raises(PromptMissing, match="not in"):
        load_prompt("no_such_prompt.md")


def test_the_user_prompt_carries_the_reference_placeholder() -> None:
    assert "{raw_reference}" in load_prompt("reference_repair.user.md")


def test_no_model_id_appears_outside_config() -> None:
    """ADR-015: `settings.model_for(role)` is the only place a model is named."""
    from pathlib import Path

    import app.parsing.segmenter as segmenter_module

    assert segmenter_module.__file__
    assert "gpt-" not in Path(segmenter_module.__file__).read_text(encoding="utf-8")
    for name in ("reference_repair.system.md", "reference_repair.user.md"):
        assert "gpt-" not in load_prompt(name)


# ---------------------------------------------------------------- the schema


def test_schema_is_strict_shaped_at_every_level() -> None:
    """OpenAI strict mode needs `additionalProperties: false` and every key required."""

    def check(node: dict[str, Any]) -> None:
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        if node.get("type") == "array":
            check(node["items"])

    check(REFERENCE_SEGMENT_SCHEMA)


def test_every_extractable_field_is_nullable() -> None:
    """A schema that *requires* a DOI is a schema instructing the model to invent one."""
    properties = REFERENCE_SEGMENT_SCHEMA["properties"]
    for name in ("title", "container_title", "page", "volume", "issue", "publisher", "doi"):
        assert properties[name]["type"] == ["string", "null"], name
    assert properties["year"]["type"] == ["integer", "null"]


def test_type_is_a_closed_vocabulary_not_free_text() -> None:
    assert "enum" in REFERENCE_SEGMENT_SCHEMA["properties"]["type"]


# ---------------------------------------------------------------- reshaping into CSL


def test_particles_survive_as_their_own_field() -> None:
    csl = csl_from_segmentation(
        _emitted(author=[{"family": "Berg", "given": "J.", "non_dropping_particle": "van der"}])
    )
    assert csl["author"] == [{"family": "Berg", "given": "J.", "non-dropping-particle": "van der"}]


def test_year_becomes_csl_date_parts() -> None:
    assert csl_from_segmentation(_emitted(year=2022))["issued"] == {"date-parts": [[2022]]}


def test_nulls_are_dropped_rather_than_stored() -> None:
    csl = csl_from_segmentation(_emitted(title="Efficient transformers: a survey"))
    assert set(csl) == {"title", "type"}


def test_empty_strings_are_dropped_not_carried() -> None:
    """An empty string passes the substring check trivially and asserts nothing."""
    csl = csl_from_segmentation(_emitted(title="   ", doi=""))
    assert "title" not in csl and "DOI" not in csl


def test_container_title_is_renamed_to_the_csl_key() -> None:
    csl = csl_from_segmentation(_emitted(container_title="ACM Comput. Surv."))
    assert csl["container-title"] == "ACM Comput. Surv."
    assert "container_title" not in csl


def test_an_author_entry_with_nothing_in_it_is_not_kept() -> None:
    csl = csl_from_segmentation(
        _emitted(author=[{"family": None, "given": None, "non_dropping_particle": None}])
    )
    assert "author" not in csl


# ---------------------------------------------------------------- the call itself


async def test_the_call_uses_the_repair_role_and_the_schema() -> None:
    client = RecordingLLM(_emitted(title="Efficient transformers: a survey"))
    await LLMReferenceSegmenter(client).segment(RAW)

    call = client.calls[0]
    assert call["role"] is LLMRole.REPAIR
    assert call["schema"] is REFERENCE_SEGMENT_SCHEMA
    assert call["system"] == load_prompt("reference_repair.system.md")
    assert RAW in call["prompt"]


async def test_an_honest_segmentation_survives_the_substring_check() -> None:
    client = RecordingLLM(
        _emitted(
            title="Efficient transformers: a survey",
            container_title="ACM Comput. Surv.",
            author=[{"family": "Berg", "given": "J.", "non_dropping_particle": "van der"}],
            year=2022,
            page="1-28",
            volume="55",
            issue="6",
        )
    )
    outcome = await repair_reference(
        ParsedReference(
            ref_id="b1",
            raw_string=RAW,
            csl=None,
            tier=ConfidenceTier.LOW_CONFIDENCE,
            parse_confidence=0.2,
        ),
        LLMReferenceSegmenter(client),
        threshold=THRESHOLD,
    )
    assert outcome.accepted, outcome.violations
    assert outcome.csl is not None and outcome.csl["container-title"] == "ACM Comput. Surv."


async def test_an_expanded_abbreviation_still_loses_the_whole_entry() -> None:
    """The schema does not — and cannot — stop this. ADR-003's check does (ADR-027)."""
    client = RecordingLLM(
        _emitted(
            title="Efficient transformers: a survey",
            container_title="ACM Computing Surveys",  # expanded; not in the raw string
        )
    )
    outcome = await repair_reference(
        ParsedReference(
            ref_id="b1",
            raw_string=RAW,
            csl=None,
            tier=ConfidenceTier.LOW_CONFIDENCE,
            parse_confidence=0.2,
        ),
        LLMReferenceSegmenter(client),
        threshold=THRESHOLD,
    )
    assert not outcome.accepted
    assert outcome.csl is None
    assert [v.path for v in outcome.violations] == ["container-title"]


async def test_the_tier_does_not_call_the_model_above_the_threshold() -> None:
    """Cheapest guarantee in the pipeline: a well-parsed entry is never sent."""
    client = RecordingLLM(_emitted(title="anything"))
    await repair_reference(
        ParsedReference(
            ref_id="b1",
            raw_string=RAW,
            csl={"title": "Efficient transformers: a survey"},
            tier=ConfidenceTier.PARSED_UNRESOLVED,
            parse_confidence=0.95,
        ),
        LLMReferenceSegmenter(client),
        threshold=THRESHOLD,
    )
    assert client.calls == []
