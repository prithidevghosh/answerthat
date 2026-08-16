"""Peer review: claim extraction, candidate generation, and quote-backed verification.

The pipeline is claim-first (ADR-005), not section-first: keyword search over section
text returns topically adjacent papers rather than the work the author should have
cited. Every stage here is scoped to one atomic claim.

    claims.py      decompose sections into atomic, citable claims (offsets, not text)
    candidates.py  three retrieval strategies, in parallel, per claim
    fusion.py      reciprocal-rank fusion, cross-provider dedupe, subtract what's cited
    prefilter.py   embeddings cut the field to RERANK_KEEP before any model call
    rerank.py      score candidates against THE CLAIM, not the topic
    verifier.py    one verifier, two callers, with the mechanical quote check (ADR-006)
    stream.py      findings streamed by citability descending, with verified/total
    prompts/       the three system prompts, as files (ADR-019)

Two invariants hold across every module here.

**Nothing in this package writes to `source_store`.** It references source_ids that a
provider adapter already wrote from a real HTTP response (HR-1).

**Nothing in this package constructs a model client.** Every call goes through
`app/core/llm.py` via `llm.py`, which is what keeps per-role routing (ADR-015),
record/replay (ADR-018) and the per-document token budget in one place.
"""
