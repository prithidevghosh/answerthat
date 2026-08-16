"""Shared helpers for B3's tests, in a uniquely named module — deliberately.

These used to live in `conftest.py` and be imported as `from conftest import …`. That
resolves through `sys.modules["conftest"]`, and `b1/`, `b2/` and `b3/` each have one, so
which module the name refers to depends on collection order: `pytest tests/unit/b3
tests/unit/b2` imported B2's conftest here and failed with a bewildering `ImportError`.
`pytest tests/unit` happens to work, which is exactly what makes it a trap — the same
shape as the b2 id-counter bleed already recorded in memory.md §5.

A unique module name is the whole fix. `conftest.py` keeps the fixtures, which pytest
resolves per-directory and which were never ambiguous.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _appendix_a import ensure_contracts_importable  # noqa: E402

ensure_contracts_importable()

from app.agent.thresholds import ReattachmentBand  # noqa: E402
from app.core.contracts import (  # noqa: E402
    AbstractSource,
    CitationAnchor,
    Document,
    DocumentMeta,
    Section,
    SourceRecord,
    Span,
    Verification,
    VerificationLabel,
)

# ADR-024's starting values, mirrored so these tests exercise the band the product ships
# with. A test probing a specific boundary builds its own band and says which number it is
# probing; nothing here reads config, because a unit test that depends on an env var is a
# test that fails for reasons unrelated to the code under it.
TEST_BAND = ReattachmentBand(accept=0.72, flag_floor=0.55)


# --------------------------------------------------------------------------- fakes



class SourceNotIndexed(RuntimeError):
    """Mirrors B2's error for a `has()` on an id that was never warmed."""


class FakeSourceReader:
    """Stands in for B2's append-only source_store. Deliberately read-only: there is no
    `put`, because `app/agent/` must not be able to create a source_id (HR-1).

    It reproduces B2's warming contract exactly, including the part that bites: `has()` on
    an id that was never warmed **raises** rather than answering False (memory.md §5,
    B2 → B3). Faking that away would let a warming bug pass here and fail in production as
    a false REJECT, so the fake keeps the sharp edge.
    """

    def __init__(self, source_ids: list[str] | None = None, *, strict: bool = False) -> None:
        self.warmed: set[str] = set()
        self.strict = strict
        self._records = {
            sid: SourceRecord(
                source_id=sid,
                csl={"id": sid, "type": "article-journal", "title": f"Paper {sid}"},
                provenance={
                    "provider": "semantic_scholar",
                    "endpoint": "/paper/search/match",
                    "retrieved_at": "2026-08-15T00:00:00Z",
                    "external_url": f"https://www.semanticscholar.org/paper/{sid}",
                },
                abstract=f"Abstract for {sid}.",
                abstract_source=AbstractSource.S2,
            )
            for sid in (source_ids or [])
        }

    async def warm(self, source_ids: list[str]) -> None:
        self.warmed.update(source_ids)

    def get(self, source_id: str) -> SourceRecord | None:
        self._assert_warmed(source_id)
        return self._records.get(source_id)

    def has(self, source_id: str) -> bool:
        self._assert_warmed(source_id)
        return source_id in self._records

    def _assert_warmed(self, source_id: str) -> None:
        """Strict mode reproduces B2's sharp edge; the default is a pre-warmed index so the
        kernel's own unit tests can stay about the kernel."""
        if self.strict and source_id not in self.warmed:
            raise SourceNotIndexed(
                f"{source_id!r} was never warmed; 'we never looked' must not be reported as "
                f"'does not exist'"
            )


class AlwaysRenders:
    def can_render(self, document):  # noqa: ANN001
        return True, None


class NeverRenders:
    def __init__(self, reason: str = "unbalanced environment") -> None:
        self.reason = reason

    def can_render(self, document):  # noqa: ANN001
        return False, self.reason


class RendersByResolvingEveryCitation:
    """What `PandocRenderProbe` actually does: build a bibliography for the whole document.

    `AlwaysRenders` never touches the store, which is why a warming gap on this path went
    unnoticed — rule 5 resolves *every* source in the after-document through the same sync
    accessor, not just the ones the change named. Mirrors `csl_lookup_for` in
    `app/api/adapters.py`, including reporting a failure as a reason rather than raising.
    """

    def __init__(self, reader) -> None:  # noqa: ANN001
        self._reader = reader

    def can_render(self, document):  # noqa: ANN001
        from app.agent.fragment import source_multiset

        try:
            for source_id in sorted(source_multiset(document)):
                self._reader.get(source_id)
        except Exception as exc:  # noqa: BLE001 — the probe reports, never raises
            return False, f"{type(exc).__name__}: {exc}"
        return True, None


# --------------------------------------------------------------------------- builders


def make_anchor(anchor_id: str, source_ids: list[str], offset: int = 0, **kwargs) -> CitationAnchor:
    return CitationAnchor(
        anchor_id=anchor_id,
        source_ids=source_ids,
        offset_in_span=offset,
        original_marker_text=kwargs.pop("marker", f"[{anchor_id}]"),
        **kwargs,
    )


def make_span(span_id: str, text: str, anchors: list[CitationAnchor] | None = None) -> Span:
    return Span(id=span_id, text=text, citation_anchors=anchors or [])


def make_doc(sections: list[Section], *, version: int = 1, doc_id: str = "doc-1") -> Document:
    return Document(doc_id=doc_id, version=version, metadata=DocumentMeta(title="A Paper"), sections=sections)


def supports(anchor_quote: str = "as shown in the abstract") -> Verification:
    return Verification(
        label=VerificationLabel.SUPPORTS,
        quote=anchor_quote,
        abstract_source=AbstractSource.S2,
        confidence=0.9,
    )


def partially_supports(anchor_quote: str = "partially relevant text") -> Verification:
    return Verification(
        label=VerificationLabel.PARTIALLY_SUPPORTS,
        quote=anchor_quote,
        abstract_source=AbstractSource.OPENALEX_INVERTED,
        confidence=0.6,
    )


def does_not_address() -> Verification:
    return Verification(
        label=VerificationLabel.DOES_NOT_ADDRESS,
        quote="unrelated",
        abstract_source=AbstractSource.S2,
        confidence=0.8,
    )


