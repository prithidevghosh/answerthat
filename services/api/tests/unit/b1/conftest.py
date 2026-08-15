"""Fixtures for B1's unit tests.

`sample_doc` is the workhorse: a small paper with two sections, a figure placeholder,
several paragraphs, and citation anchors including one span carrying two anchors and one
anchor citing two sources. That shape is chosen on purpose — single-anchor single-source
documents pass round-trip and multiset tests that a real paper would fail.
"""

from __future__ import annotations

import pytest

from app.core.contracts import Document
from app.ir.builder import DocumentBuilder


@pytest.fixture
def sample_doc() -> Document:
    b = DocumentBuilder("doc_test0001", title="Attention Considered Expensive")

    intro = b.section("Introduction", level=1)
    para1, s1 = intro.paragraph(
        "Transformer models dominate sequence modelling. Their quadratic attention cost "
        "has motivated a long line of efficiency work."
    )
    # One span carrying two anchors, one of which cites two sources.
    para1.anchor(s1, source_ids=["src_vaswani"], offset_in_span=46, original_marker_text="[1]")
    para1.anchor(s1, source_ids=["src_tay", "src_child"], offset_in_span=123, original_marker_text="[2, 3]")

    intro.paragraph("We revisit that assumption on modern hardware.")

    method = b.section("Method", level=1)
    para3, s3 = method.paragraph("We follow the standard training recipe with no modifications.")
    # src_vaswani is now cited twice across the document — a multiset, not a set.
    para3.anchor(s3, source_ids=["src_vaswani"], offset_in_span=60, original_marker_text="[1]")
    method.placeholder("figure", "Figure 1: Throughput against sequence length.")

    b.quarantine("Smith, J. mumble mumble 20??, pp. ??-??", "parse_failed", page=9)
    return b.build()


@pytest.fixture
def known_source_ids() -> set[str]:
    return {"src_vaswani", "src_tay", "src_child"}
