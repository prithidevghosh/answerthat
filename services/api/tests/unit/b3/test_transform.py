"""Detach → transform → reattach (ADR-013)."""

from __future__ import annotations

import pytest
from conftest import make_anchor, make_span
from fakes import BagOfWordsEmbedder, FakeFingerprintStore, ScriptedTextModel

from app.agent.thresholds import ReattachmentBand
from app.agent.transform import (
    DetachTransformReattach,
    MarkerLeakError,
    host_sentence_for,
    scrub_markers,
    split_sentences,
    strip_marker_leaks,
)
from app.core.contracts import Block


def block_with_two_cited_sentences() -> Block:
    return Block(
        id="blk-1",
        type="paragraph",
        order=0,
        spans=[
            make_span(
                "span-1",
                "Transformers dominate sequence modelling across natural language tasks.",
                [make_anchor("anc-1", ["s2:aaa"], offset=71, marker="[1]")],
            ),
            make_span(
                "span-2",
                "Attention memory grows quadratically with the input length.",
                [make_anchor("anc-2", ["s2:bbb"], offset=59, marker="[2]")],
            ),
        ],
    )


def pipeline(
    output: str,
    *,
    accept: float = 0.82,
    flag_floor: float = 0.55,
    fingerprints: FakeFingerprintStore | None = None,
) -> tuple[DetachTransformReattach, ScriptedTextModel]:
    """The band is passed explicitly, never defaulted inside the pipeline (ADR-024).

    Tests name the numbers they are probing, which is the point of having names for them —
    the production values come from `app/core/config.py` and are swept, not asserted here.
    """
    text_model = ScriptedTextModel(output)
    dtr = DetachTransformReattach(
        BagOfWordsEmbedder(),
        text_model,
        fingerprints or FakeFingerprintStore(),
        band=ReattachmentBand(accept=accept, flag_floor=flag_floor),
    )
    return dtr, text_model


# --------------------------------------------------------------------------- text utils


def test_split_sentences_survives_academic_abbreviations():
    text = "Prior work (Smith et al. 2019) used e.g. sparse kernels. We do not. See Fig. 2 for detail."
    assert split_sentences(text) == [
        "Prior work (Smith et al. 2019) used e.g. sparse kernels.",
        "We do not.",
        "See Fig. 2 for detail.",
    ]


def test_host_sentence_is_the_one_the_anchor_sits_in():
    text = "First claim here. Second claim here. Third claim here."
    assert host_sentence_for(text, 17) == "First claim here."
    assert host_sentence_for(text, 36) == "Second claim here."


def test_scrub_markers_removes_a_marker_left_in_prose():
    span = make_span("s", "Attention is quadratic [12].", [make_anchor("a", ["s2:aaa"], 23, marker="[12]")])
    assert "[12]" not in scrub_markers(span.text, span.citation_anchors)


def test_strip_marker_leaks_reports_what_it_removed():
    cleaned, leaks = strip_marker_leaks("Sparse attention helps [4]. See \\citep{x} too.")
    assert leaks == ["[4]", "\\citep{"]
    assert "[4]" not in cleaned


# --------------------------------------------------------------------------- step 2


@pytest.mark.asyncio
async def test_the_text_model_never_sees_a_citation_marker():
    """ADR-013 step 2, the whole trick: you cannot lose a citation you never showed."""
    block = block_with_two_cited_sentences()
    block.spans[0].text = "Transformers dominate sequence modelling [1]."
    dtr, model = pipeline("Transformers dominate sequence modelling. Attention memory is quadratic.")

    await dtr.run(block, system="sys", instruction="shorten")

    assert model.texts_seen, "the text model was never called"
    for seen in model.texts_seen:
        assert "[1]" not in seen
        assert "[2]" not in seen
        assert "\\cite" not in seen


@pytest.mark.asyncio
async def test_a_marker_reaching_the_text_model_is_a_loud_failure():
    dtr, _model = pipeline("anything")
    with pytest.raises(MarkerLeakError):
        await dtr.transform("Attention is quadratic [12].", system="sys", instruction="x")


@pytest.mark.asyncio
async def test_marker_shaped_output_from_the_model_is_stripped_and_reported():
    block = block_with_two_cited_sentences()
    dtr, _model = pipeline(
        "Transformers dominate sequence modelling across natural language tasks [7]. "
        "Attention memory grows quadratically with the input length."
    )
    report = await dtr.run(block, system="sys", instruction="shorten")
    assert report.marker_leaks == ["[7]"]
    assert all("[7]" not in span.text for span in report.new_spans)


# --------------------------------------------------------------------------- steps 3 & 4


@pytest.mark.asyncio
async def test_anchors_reattach_to_the_sentence_they_belong_to():
    block = block_with_two_cited_sentences()
    dtr, _model = pipeline(
        "Attention memory grows quadratically with the input length. "
        "Transformers dominate sequence modelling across natural language tasks."
    )
    report = await dtr.run(block, system="sys", instruction="reorder")

    landed = {r.anchor_id: r.landed_span_id for r in report.reattachments}
    by_span = {span.id: span for span in report.new_spans}

    # The order flipped; each anchor must have followed its own sentence, not its index.
    assert "quadratically" in by_span[landed["anc-2"]].text
    assert "dominate" in by_span[landed["anc-1"]].text
    assert report.orphaned_anchors == []


@pytest.mark.asyncio
async def test_every_anchor_survives_a_compression():
    block = block_with_two_cited_sentences()
    dtr, _model = pipeline(
        "Transformers dominate sequence modelling across language tasks. "
        "Attention memory grows quadratically with input length."
    )
    report = await dtr.run(block, system="sys", instruction="shorten")

    attached = {a.anchor_id for span in report.new_spans for a in span.citation_anchors}
    assert attached | set(report.orphaned_anchor_ids) == {"anc-1", "anc-2"}


@pytest.mark.asyncio
async def test_an_anchor_with_no_home_is_surfaced_not_dropped():
    """The residual hard case becomes a visible user decision (ADR-013 step 4)."""
    block = block_with_two_cited_sentences()
    dtr, _model = pipeline("We evaluate on eight downstream benchmarks and report accuracy.")
    report = await dtr.run(block, system="sys", instruction="replace")

    assert set(report.orphaned_anchor_ids) == {"anc-1", "anc-2"}
    assert all(r.landed_span_id is None for r in report.reattachments)
    assert all(r.score is not None for r in report.reattachments), "the near-miss score is reported"


@pytest.mark.asyncio
async def test_a_model_returning_nothing_orphans_every_anchor_rather_than_losing_it():
    block = block_with_two_cited_sentences()
    dtr, _model = pipeline("   ")
    report = await dtr.run(block, system="sys", instruction="shorten")

    assert report.new_spans == []
    assert set(report.orphaned_anchor_ids) == {"anc-1", "anc-2"}


@pytest.mark.asyncio
async def test_reattachment_records_the_score_it_attached_at():
    block = block_with_two_cited_sentences()
    dtr, _model = pipeline(
        "Transformers dominate sequence modelling across natural language tasks. "
        "Attention memory grows quadratically with the input length."
    )
    report = await dtr.run(block, system="sys", instruction="noop")
    for record in report.reattachments:
        assert record.score is not None and 0.0 <= record.score <= 1.0
        assert record.landed_span_id is not None


@pytest.mark.asyncio
async def test_fingerprints_are_recorded_at_detach_time():
    block = block_with_two_cited_sentences()
    store = FakeFingerprintStore()
    dtr, _model = pipeline("anything at all here", fingerprints=store)
    _prose, detached = await dtr.detach(block)

    assert all(d.fingerprint_id for d in detached)
    assert all(d.vector for d in detached)
    assert [d.host_sentence for d in detached] == [
        "Transformers dominate sequence modelling across natural language tasks.",
        "Attention memory grows quadratically with the input length.",
    ]
    # The sentence is stored beside the vector, so "why did this anchor reattach there?"
    # stays an answerable question.
    assert [text for _v, text in store.rows.values()] == [d.host_sentence for d in detached]


@pytest.mark.asyncio
async def test_the_vector_goes_to_the_side_table_and_the_ir_gets_an_id():
    """ADR-017. A diff in which every anchor is 512 floats is unreadable, and that diff is
    how the user verifies HR-5 with their own eyes."""
    block = block_with_two_cited_sentences()
    store = FakeFingerprintStore()
    dtr, _model = pipeline(
        "Transformers dominate sequence modelling across natural language tasks. "
        "Attention memory grows quadratically with the input length.",
        fingerprints=store,
    )
    report = await dtr.run(block, system="sys", instruction="noop")

    attached = [a for span in report.new_spans for a in span.citation_anchors]
    assert attached, "the anchors did not reattach, so this test proves nothing"
    for anchor in attached:
        assert anchor.fingerprint_id in store.rows
        assert not hasattr(anchor, "context_fingerprint")
        # The vector is nowhere in the serialised IR the user will diff.
        assert "vector" not in anchor.model_dump()


@pytest.mark.asyncio
async def test_an_anchor_that_already_has_a_fingerprint_is_not_re_embedded():
    """Fingerprints are immutable and shared across versions (ADR-017): the sentence an
    anchor *was* recorded against is what we match, not whatever it drifted into."""
    store = FakeFingerprintStore()
    recorded = await store.put(
        vector=(await BagOfWordsEmbedder().embed(["Attention memory grows quadratically."]))[0],
        text="Attention memory grows quadratically.",
    )
    store.puts.clear()

    block = Block(
        id="blk-1",
        type="paragraph",
        order=0,
        spans=[
            make_span(
                "span-1",
                "Attention memory grows quadratically with the input length.",
                [make_anchor("anc-1", ["s2:bbb"], offset=59, fingerprint_id=recorded)],
            )
        ],
    )
    dtr, _model = pipeline("unused", fingerprints=store)
    _prose, detached = await dtr.detach(block)

    assert store.puts == [], "an anchor with a recorded fingerprint was re-embedded"
    assert detached[0].fingerprint_id == recorded


@pytest.mark.asyncio
async def test_a_middling_score_attaches_but_is_flagged_rather_than_surfaced():
    """The band between the floor and the accept threshold is the point of having two
    numbers: placed, but shown to the user to check (ADR-024)."""
    block = block_with_two_cited_sentences()
    # The compression keeps each sentence recognisable but drops half its vocabulary, so
    # both anchors land at ~0.71 — under the accept bar, over the floor.
    dtr, _model = pipeline(
        "Transformers dominate sequence modelling. Attention memory grows quadratically.",
        accept=0.82,
        flag_floor=0.55,
    )
    report = await dtr.run(block, system="sys", instruction="shorten")

    assert report.orphaned_anchors == [], "a score above the floor must not become a prompt"
    assert all(r.landed_span_id is not None for r in report.reattachments)
    assert all(r.score is not None and r.score < r.threshold for r in report.reattachments), (
        "these placements are the ones the kernel must FLAG"
    )


@pytest.mark.asyncio
async def test_below_the_floor_nothing_is_proposed_and_the_user_decides():
    block = block_with_two_cited_sentences()
    # The same ~0.71 placements, judged against a floor they do not clear.
    dtr, _model = pipeline(
        "Transformers dominate sequence modelling. Attention memory grows quadratically.",
        accept=0.90,
        flag_floor=0.80,
    )
    report = await dtr.run(block, system="sys", instruction="shorten")

    assert set(report.orphaned_anchor_ids) == {"anc-1", "anc-2"}
    for record in report.reattachments:
        assert record.landed_span_id is None
        assert record.best_span_id is not None, "the near-miss span is kept so 'keep here' works"
        assert record.flag_floor == 0.80


@pytest.mark.asyncio
async def test_derivation_is_recorded_for_every_new_span():
    """The kernel needs this to tell a rewrite from an invention."""
    block = block_with_two_cited_sentences()
    dtr, _model = pipeline("Transformers dominate. Attention is quadratic.")
    report = await dtr.run(block, system="sys", instruction="shorten")
    for span in report.new_spans:
        assert report.derived_spans[span.id] == ["span-1", "span-2"]
