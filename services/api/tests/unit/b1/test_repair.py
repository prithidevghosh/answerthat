"""The constrained repair tier. ADR-003.

The tests that matter here are the adversarial ones. A repair tier that passes its happy
path proves nothing — the whole point of the mechanism is what it does when the model
misbehaves, so most of this file is a model misbehaving in a different way each time.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.contracts import ConfidenceTier, ParsedReference
from app.core.errors import MissingAPIKeyError
from app.parsing.repair import (
    check_substring_containment,
    normalise_for_containment,
    repair_reference,
    repair_references,
    require_model_credentials,
)

THRESHOLD = 0.75

RAW = 'J. van der Berg, "Efficient transformers: a survey," ACM Comput. Surv., vol. 55, no. 6, pp. 1-28, 2022.'


class FakeSegmenter:
    """Returns whatever it was told to, so each test can specify a misbehaviour."""

    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.calls: list[str] = []

    async def segment(self, raw_string: str) -> dict[str, Any]:
        self.calls.append(raw_string)
        return self.output


def _reference(confidence: float = 0.30, raw: str = RAW) -> ParsedReference:
    return ParsedReference(
        ref_id="b7",
        raw_string=raw,
        csl=None,
        tier=ConfidenceTier.LOW_CONFIDENCE,
        parse_confidence=confidence,
    )


HONEST_OUTPUT = {
    "type": "article-journal",
    "title": "Efficient transformers: a survey",
    "container-title": "ACM Comput. Surv.",
    "author": [{"family": "Berg", "given": "J.", "non-dropping-particle": "van der"}],
    "issued": {"date-parts": [[2022]]},
    "volume": "55",
    "issue": "6",
    "page": "1-28",
}


# ---------------------------------------------------------------- normalisation


def test_normalisation_folds_case_punctuation_and_accents() -> None:
    assert normalise_for_containment("Éfficient, Transformers!") == "efficient transformers"
    assert normalise_for_containment("  pp.  1-28 ") == "pp 1 28"


def test_normalisation_does_not_merge_separate_words() -> None:
    """'van der Berg' must not fold into 'vanderberg' — that would let a real
    fabrication through by collapsing the boundary between tokens."""
    assert normalise_for_containment("van der Berg") == "van der berg"
    assert "vanderberg" not in normalise_for_containment("van der Berg")


# ---------------------------------------------------------------- the substring check


def test_honest_segmentation_passes() -> None:
    assert check_substring_containment(HONEST_OUTPUT, RAW) == []


def test_invented_author_is_caught() -> None:
    """The case ADR-003 exists for."""
    output = {**HONEST_OUTPUT, "author": [{"family": "Vaswani", "given": "Ashish"}]}
    violations = check_substring_containment(output, RAW)
    assert [v.value for v in violations] == ["Vaswani", "Ashish"]


def test_expanded_abbreviation_is_caught() -> None:
    """'ACM Comput. Surv.' → 'ACM Computing Surveys' is *correct* and still rejected.

    The expansion is a fact about the world the model supplied, not a fact about the
    string in front of it, and no mechanism can tell that apart from an invented author.
    """
    output = {**HONEST_OUTPUT, "container-title": "ACM Computing Surveys"}
    assert [v.path for v in check_substring_containment(output, RAW)] == ["container-title"]


def test_corrected_typo_is_caught() -> None:
    output = {**HONEST_OUTPUT, "title": "Efficient transformers: a review"}
    assert check_substring_containment(output, RAW)


def test_invented_doi_is_caught() -> None:
    output = {**HONEST_OUTPUT, "DOI": "10.1145/3530811"}
    assert [v.path for v in check_substring_containment(output, RAW)] == ["DOI"]


def test_invented_year_is_caught() -> None:
    output = {**HONEST_OUTPUT, "issued": {"date-parts": [[2021]]}}
    assert check_substring_containment(output, RAW)


def test_type_is_exempt_but_validated_against_the_vocabulary() -> None:
    """'article-journal' is a CSL term, not text from the reference — but it must be a
    real CSL term."""
    assert check_substring_containment({"type": "article-journal"}, RAW) == []
    violations = check_substring_containment({"type": "definitely-not-a-csl-type"}, RAW)
    assert violations and "vocabulary" in violations[0].reason


def test_nested_values_are_all_checked() -> None:
    """A violation buried in the third author must not escape."""
    output = {
        **HONEST_OUTPUT,
        "author": [
            {"family": "Berg", "given": "J."},
            {"family": "Fabricated", "given": "Person"},
        ],
    }
    paths = [v.path for v in check_substring_containment(output, RAW)]
    assert "author[1].family" in paths


# ---------------------------------------------------------------- the tier


async def test_repair_runs_only_below_threshold() -> None:
    segmenter = FakeSegmenter(HONEST_OUTPUT)
    outcome = await repair_reference(_reference(confidence=0.90), segmenter, threshold=THRESHOLD)
    assert not outcome.ran
    assert segmenter.calls == [], "the model must not be called for a confident parse"
    assert "at or above threshold" in outcome.skipped_reason


async def test_repair_accepts_an_honest_segmentation() -> None:
    outcome = await repair_reference(_reference(), FakeSegmenter(HONEST_OUTPUT), threshold=THRESHOLD)
    assert outcome.accepted
    assert outcome.csl["title"] == "Efficient transformers: a survey"
    assert outcome.csl["note"] == RAW, "the raw string travels with the record"
    assert outcome.violations == []


async def test_repair_rejects_the_whole_entry_on_any_violation() -> None:
    """ADR-003, strictly: the value is discarded *and* the entry is marked unparsed.

    A model that invented one field has demonstrated it will invent. There is no
    principled way to trust the remaining fields of the same output.
    """
    dishonest = {**HONEST_OUTPUT, "author": [{"family": "Vaswani", "given": "Ashish"}]}
    outcome = await repair_reference(_reference(), FakeSegmenter(dishonest), threshold=THRESHOLD)
    assert not outcome.accepted
    assert outcome.csl is None
    assert outcome.violations


async def test_repair_refuses_to_run_without_a_raw_string() -> None:
    """No raw string means no way to check the output, and an unchecked repair is
    precisely what ADR-003 forbids."""
    segmenter = FakeSegmenter(HONEST_OUTPUT)
    outcome = await repair_reference(_reference(raw="  "), segmenter, threshold=THRESHOLD)
    assert not outcome.ran
    assert segmenter.calls == []
    assert "substring check cannot run" in outcome.skipped_reason


async def test_unexpected_keys_are_dropped_not_stored() -> None:
    segmenter = FakeSegmenter({**HONEST_OUTPUT, "abstract": "Efficient transformers"})
    outcome = await repair_reference(_reference(), segmenter, threshold=THRESHOLD)
    assert outcome.accepted
    assert "abstract" not in outcome.csl


async def test_segmentation_with_no_title_or_doi_is_not_accepted() -> None:
    outcome = await repair_reference(
        _reference(), FakeSegmenter({"volume": "55"}), threshold=THRESHOLD
    )
    assert not outcome.accepted
    assert "neither a title nor a DOI" in outcome.skipped_reason


# ---------------------------------------------------------------- across a bibliography


async def test_rejected_repair_is_quarantined_with_its_raw_string_intact() -> None:
    """HR-3: no reference is ever dropped, and quarantined entries keep their raw text."""
    dishonest = {**HONEST_OUTPUT, "title": "A Completely Different Paper"}
    references = [_reference()]
    updated, outcomes = await repair_references(
        references, FakeSegmenter(dishonest), threshold=THRESHOLD
    )
    assert len(updated) == len(references)
    assert updated[0].tier == ConfidenceTier.QUARANTINED
    assert updated[0].raw_string == RAW
    assert outcomes[0].violations


async def test_accepted_repair_lifts_the_tier() -> None:
    updated, outcomes = await repair_references(
        [_reference()], FakeSegmenter(HONEST_OUTPUT), threshold=THRESHOLD
    )
    assert updated[0].tier == ConfidenceTier.PARSED_UNRESOLVED
    assert updated[0].parse_confidence > 0.30
    assert outcomes[0].accepted


async def test_skipped_and_failed_are_distinguishable_in_the_outcomes() -> None:
    """Telemetry needs to tell 'did not run' from 'ran and failed' — ADR-002's revisit
    condition depends on the difference."""
    references = [_reference(confidence=0.95), _reference(confidence=0.10)]
    _, outcomes = await repair_references(
        references, FakeSegmenter({"title": "nonsense not in the string"}), threshold=THRESHOLD
    )
    assert [o.ran for o in outcomes] == [False, True]
    assert not outcomes[1].accepted


def test_missing_model_key_raises_rather_than_skipping_the_tier() -> None:
    with pytest.raises(MissingAPIKeyError, match="OPENAI_API_KEY"):
        require_model_credentials("")
    assert require_model_credentials("sk-real") == "sk-real"
