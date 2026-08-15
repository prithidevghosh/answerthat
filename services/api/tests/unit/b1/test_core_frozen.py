"""`app/core/` is frozen. These tests are the lock.

CP-1 requires that `app/core/` matches goal.md Appendix A *exactly*. "Exactly" is only
meaningful if it is checked mechanically, so this reads the Appendix A code block out of
goal.md and compares it byte for byte with `contracts.py`.

If this test fails, one of two things happened: someone edited contracts.py without an
ADR, or Appendix A itself changed. Neither is fixed by editing this test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core import contracts

REPO_ROOT = Path(__file__).resolve().parents[5]
GOAL_MD = REPO_ROOT / "goal.md"
CONTRACTS_PY = REPO_ROOT / "services" / "api" / "app" / "core" / "contracts.py"


def _appendix_a_source() -> str:
    text = GOAL_MD.read_text(encoding="utf-8")
    assert "## Appendix A — Frozen contracts" in text, "Appendix A heading not found in goal.md"
    appendix = text.split("## Appendix A — Frozen contracts", 1)[1]
    blocks = re.findall(r"```python\n(.*?)```", appendix, re.S)
    assert len(blocks) == 1, f"expected exactly one python block in Appendix A, found {len(blocks)}"
    return blocks[0]


def test_contracts_py_is_appendix_a_verbatim() -> None:
    assert CONTRACTS_PY.read_text(encoding="utf-8") == _appendix_a_source()


@pytest.mark.parametrize(
    "name",
    [
        # errors
        "MissingAPIKeyError", "ProviderRateLimited", "ParseFailure", "KernelRejection",
        # sources
        "AbstractSource", "Provenance", "SourceRecord", "SourceStore",
        # document IR
        "CitationAnchor", "Span", "Block", "Section", "QuarantineEntry",
        "DocumentMeta", "Document",
        # parsing
        "ConfidenceTier", "ParsedReference",
        # review
        "Claim", "Candidate", "VerificationLabel", "Verification", "Finding",
        # agent
        "OperationType", "Operation", "EditPlan", "ProposedChange", "KernelVerdict",
        # providers
        "Provider",
    ],
)
def test_every_appendix_a_symbol_is_importable(name: str) -> None:
    """B2 and B3 import these by name. None of them may quietly disappear."""
    assert hasattr(contracts, name), f"app.core.contracts.{name} is missing"


def test_all_five_confidence_tiers_exist() -> None:
    """HR-3: five tiers, no more, no fewer. A sixth would be an unmodelled state."""
    assert {t.value for t in contracts.ConfidenceTier} == {
        "resolved",
        "parsed_unresolved",
        "low_confidence",
        "quarantined",
        "orphan_marker",
    }


def test_all_seven_operations_exist() -> None:
    """CP-6 names seven. FreeformEdit is the gated seventh (ADR-009)."""
    assert {o.value for o in contracts.OperationType} == {
        "AddCitations",
        "FindSupport",
        "Shorten",
        "RewriteSection",
        "ReplaceCitation",
        "MoveText",
        "FreeformEdit",
    }


def test_abstract_fallback_chain_is_representable() -> None:
    """S2 -> OpenAlex inverted -> TLDR -> unavailable. `unavailable` is a real outcome."""
    assert {a.value for a in contracts.AbstractSource} == {
        "s2",
        "openalex_inverted",
        "tldr",
        "unavailable",
    }
    assert contracts.SourceRecord.model_fields["abstract_source"].default is (
        contracts.AbstractSource.UNAVAILABLE
    ), "a record with no abstract must default to unavailable, never to a plausible source"


def test_errors_module_reexports_the_same_classes() -> None:
    """One definition each — `errors.py` must not shadow contracts with a lookalike."""
    from app.core import errors

    for name in ("MissingAPIKeyError", "ProviderRateLimited", "ParseFailure", "KernelRejection"):
        assert getattr(errors, name) is getattr(contracts, name)
