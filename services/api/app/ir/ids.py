"""Stable identifiers for IR nodes.

Two flavours, and the difference matters:

* `stable_id(...)` is a content-addressed hash of the node's position in the source
  document. Re-parsing the same PDF produces the same IDs, which is what makes a diff
  between two parses readable and what lets a fixture assert on an anchor by name.
* `new_id(...)` is random. Use it for nodes the agent creates at edit time, where there
  is no source position to hash.

Anchor IDs in particular are load-bearing: HR-5 is stated in terms of anchors surviving
an edit, and an anchor whose ID changed is indistinguishable from one that was dropped
and replaced.
"""

from __future__ import annotations

import hashlib
import uuid

__all__ = [
    "SECTION", "BLOCK", "SPAN", "ANCHOR", "REFERENCE", "DOCUMENT",
    "stable_id", "new_id", "prefix_of",
]

SECTION = "sec"
BLOCK = "blk"
SPAN = "spn"
ANCHOR = "anc"
REFERENCE = "ref"
DOCUMENT = "doc"

_DIGEST_CHARS = 12


def stable_id(prefix: str, *parts: object) -> str:
    """Deterministic ID from a node's structural path.

    `parts` should identify the node's position uniquely and stably — e.g.
    `stable_id(SPAN, doc_id, section_order, block_order, span_index)`. Passing the
    node's *text* is usually a mistake: editing a paragraph would then change its ID,
    and the whole point is that it does not.
    """
    payload = "\x1f".join(str(p) for p in parts).encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=16).hexdigest()[:_DIGEST_CHARS]
    return f"{prefix}_{digest}"


def new_id(prefix: str) -> str:
    """Random ID, for nodes created during editing rather than parsed from a source."""
    return f"{prefix}_{uuid.uuid4().hex[:_DIGEST_CHARS]}"


def prefix_of(node_id: str) -> str:
    """The kind prefix of an ID, or "" if it has none.

    Useful for asserting that an operation targeting a span was not handed a section.
    """
    head, sep, _ = node_id.partition("_")
    return head if sep else ""
