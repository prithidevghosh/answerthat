"""Fixtures only. The importable helpers live in `b3_support.py` — see the note there
about why they are not in this file.
"""

from __future__ import annotations

import pytest
from b3_support import AlwaysRenders, FakeSourceReader, make_anchor, make_doc, make_span

from app.core.contracts import Block, Document, Section

# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def sources() -> FakeSourceReader:
    return FakeSourceReader(["s2:aaa", "s2:bbb", "openalex:W123"])


@pytest.fixture
def base_document() -> Document:
    intro = Section(
        id="sec-1",
        level=1,
        title="Introduction",
        order=0,
        blocks=[
            Block(
                id="blk-1",
                type="paragraph",
                order=0,
                spans=[
                    make_span(
                        "span-1",
                        "Transformers dominate sequence modelling.",
                        [make_anchor("anc-1", ["s2:aaa"], offset=41)],
                    ),
                    make_span(
                        "span-2",
                        "Attention scales quadratically with length.",
                        [make_anchor("anc-2", ["s2:bbb"], offset=43)],
                    ),
                ],
            )
        ],
    )
    method = Section(
        id="sec-2",
        level=1,
        title="Method",
        order=1,
        blocks=[
            Block(
                id="blk-2",
                type="paragraph",
                order=0,
                spans=[make_span("span-3", "We train on a single GPU.")],
            )
        ],
    )
    return make_doc([intro, method])


@pytest.fixture
def kernel(sources: FakeSourceReader):
    from app.agent.kernel import InvariantKernel

    return InvariantKernel(sources, AlwaysRenders())
