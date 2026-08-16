"""The LLM client: per-role routing, structured output, record/replay, token budget.

ADR-018's whole value is that a missing recording **fails**. So the test that matters
most here is the one asserting replay does not fall through to the network — everything
else is scaffolding around that promise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.contracts import LLMRole, MissingAPIKeyError
from app.core.llm import (
    LLMRecordingMissing,
    OpenAILLMClient,
    Recorder,
    StructuredOutputError,
    TokenBudget,
    TokenBudgetExceeded,
    recording_key,
)

SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}


class FakeSettings:
    """Only what the client reads."""

    openai_api_key = "sk-test"
    llm_mode = "live"
    llm_timeout_s = 30.0
    llm_max_retries = 3
    embedding_model = "text-embedding-3-small"
    embedding_dimensions = 512
    doc_token_budget = 1000

    model_repair = "gpt-5.4-mini"
    model_claim_extraction = "gpt-5.4"
    model_rerank = "gpt-5.4-mini"
    model_verify = "gpt-5.5"
    model_plan = "gpt-5.5"
    model_transform = "gpt-5.4"

    def __init__(self, recordings: Path, mode: str = "live", key: str = "sk-test") -> None:
        self.llm_recordings_dir = recordings
        self.llm_mode = mode
        self.openai_api_key = key

    def model_for(self, role: LLMRole) -> str:
        return {
            LLMRole.REPAIR: self.model_repair,
            LLMRole.CLAIM_EXTRACTION: self.model_claim_extraction,
            LLMRole.RERANK: self.model_rerank,
            LLMRole.VERIFY: self.model_verify,
            LLMRole.PLAN: self.model_plan,
            LLMRole.TRANSFORM: self.model_transform,
        }[role]


class FakeOpenAI:
    """Minimal stand-in for the SDK surface the client touches."""

    def __init__(self, content: str = '{"title": "ok"}', finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.chat = self._Chat(self)
        self.embeddings = self._Embeddings(self)
        self.completion_calls: list[dict] = []
        self.embedding_calls: list[dict] = []

    class _Chat:
        def __init__(self, outer: FakeOpenAI) -> None:
            self.completions = self
            self._outer = outer

        async def create(self, **kwargs):
            self._outer.completion_calls.append(kwargs)
            outer = self._outer

            class Message:
                content = outer.content

            class Choice:
                message = Message()
                finish_reason = outer.finish_reason

            class Usage:
                total_tokens = 42

            class Response:
                choices = [Choice()]
                usage = Usage()

            return Response()

    class _Embeddings:
        def __init__(self, outer: FakeOpenAI) -> None:
            self._outer = outer

        async def create(self, **kwargs):
            self._outer.embedding_calls.append(kwargs)
            texts = kwargs["input"]
            dims = kwargs["dimensions"]

            class Item:
                def __init__(self, i: int) -> None:
                    self.embedding = [float(i)] * dims

            class Response:
                data = [Item(i) for i in range(len(texts))]

            return Response()


def _client(tmp_path: Path, mode: str = "live", fake: FakeOpenAI | None = None, **kw):
    return OpenAILLMClient(
        FakeSettings(tmp_path, mode=mode, **kw), client=fake or FakeOpenAI()
    )


# ---------------------------------------------------------------- routing


async def test_each_role_uses_its_pinned_model(tmp_path: Path) -> None:
    fake = FakeOpenAI()
    client = _client(tmp_path, fake=fake)
    for role in LLMRole:
        await client.complete(role, "prompt", SCHEMA)
    used = [call["model"] for call in fake.completion_calls]
    assert used == ["gpt-5.4-mini", "gpt-5.4", "gpt-5.4-mini", "gpt-5.5", "gpt-5.5", "gpt-5.4"]


async def test_structured_output_is_mandatory(tmp_path: Path) -> None:
    """JSON Schema, not prompt-and-parse — this is what makes ADR-007 true by construction."""
    fake = FakeOpenAI()
    await _client(tmp_path, fake=fake).complete(LLMRole.PLAN, "p", SCHEMA, system="s")
    fmt = fake.completion_calls[0]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == SCHEMA
    assert fake.completion_calls[0]["messages"][0]["role"] == "system"


async def test_invalid_json_raises_rather_than_returning_text(tmp_path: Path) -> None:
    client = _client(tmp_path, fake=FakeOpenAI(content="I'm afraid I can't do that"))
    with pytest.raises(StructuredOutputError, match="not valid JSON"):
        await client.complete(LLMRole.VERIFY, "p", SCHEMA)


async def test_truncated_output_is_refused(tmp_path: Path) -> None:
    """A truncated JSON body can still parse into something plausible. Refuse it."""
    client = _client(tmp_path, fake=FakeOpenAI(content='{"title": "ok"}', finish_reason="length"))
    with pytest.raises(StructuredOutputError, match="truncated"):
        await client.complete(LLMRole.VERIFY, "p", SCHEMA)


# ---------------------------------------------------------------- record / replay


async def test_record_then_replay_round_trips(tmp_path: Path) -> None:
    recorded = _client(tmp_path, mode="record", fake=FakeOpenAI(content='{"title": "recorded"}'))
    first = await recorded.complete(LLMRole.REPAIR, "the prompt", SCHEMA)
    assert first == {"title": "recorded"}
    assert list(tmp_path.glob("*.json")), "record mode wrote nothing"

    replayed = _client(tmp_path, mode="replay", fake=FakeOpenAI(content='{"title": "LIVE"}'))
    second = await replayed.complete(LLMRole.REPAIR, "the prompt", SCHEMA)
    assert second == {"title": "recorded"}, "replay served the live answer instead of the recording"


async def test_replay_raises_on_a_miss_and_does_not_call_the_api(tmp_path: Path) -> None:
    """ADR-018's whole point. A cache miss must fail the build, not hit the network."""
    fake = FakeOpenAI()
    client = _client(tmp_path, mode="replay", fake=fake)
    with pytest.raises(LLMRecordingMissing) as exc:
        await client.complete(LLMRole.VERIFY, "never recorded", SCHEMA)
    assert fake.completion_calls == [], "replay fell through to the API on a miss"
    assert "LLM_MODE=record" in str(exc.value)


async def test_replay_needs_no_api_key(tmp_path: Path) -> None:
    """CI must run offline, with no key present at all."""
    recorded = _client(tmp_path, mode="record", fake=FakeOpenAI(content='{"title": "x"}'))
    await recorded.complete(LLMRole.RERANK, "p", SCHEMA)

    keyless = OpenAILLMClient(FakeSettings(tmp_path, mode="replay", key=""))
    assert await keyless.complete(LLMRole.RERANK, "p", SCHEMA) == {"title": "x"}


async def test_a_missing_key_raises_when_a_live_call_is_attempted(tmp_path: Path) -> None:
    keyless = OpenAILLMClient(FakeSettings(tmp_path, mode="live", key=""))
    with pytest.raises(MissingAPIKeyError, match="OPENAI_API_KEY"):
        await keyless.complete(LLMRole.PLAN, "p", SCHEMA)


def test_the_recording_key_includes_the_model(tmp_path: Path) -> None:
    """Otherwise a model change would silently pass CI on stale recordings."""
    a = recording_key(LLMRole.VERIFY, "gpt-5.5", "p", SCHEMA, None)
    b = recording_key(LLMRole.VERIFY, "gpt-5.4", "p", SCHEMA, None)
    assert a != b
    assert a == recording_key(LLMRole.VERIFY, "gpt-5.5", "p", SCHEMA, None)


def test_the_recording_key_covers_prompt_schema_and_system(tmp_path: Path) -> None:
    base = recording_key(LLMRole.PLAN, "m", "p", SCHEMA, None)
    assert base != recording_key(LLMRole.PLAN, "m", "different", SCHEMA, None)
    assert base != recording_key(LLMRole.PLAN, "m", "p", {"type": "string"}, None)
    assert base != recording_key(LLMRole.PLAN, "m", "p", SCHEMA, "a system prompt")


async def test_a_corrupt_recording_raises_rather_than_falling_back(tmp_path: Path) -> None:
    """A broken fixture must not be indistinguishable from a cache miss."""
    key = recording_key(LLMRole.REPAIR, "gpt-5.4-mini", "p", SCHEMA, None)
    Recorder(tmp_path).path_for(key).write_text("{not json", encoding="utf-8")
    client = _client(tmp_path, mode="replay")
    with pytest.raises(Exception, match="not valid JSON"):
        await client.complete(LLMRole.REPAIR, "p", SCHEMA)


# ---------------------------------------------------------------- embeddings


async def test_embeddings_are_512_dimensional(tmp_path: Path) -> None:
    fake = FakeOpenAI()
    vectors = await _client(tmp_path, fake=fake).embed(["one", "two"])
    assert len(vectors) == 2
    assert all(len(v) == 512 for v in vectors)
    assert fake.embedding_calls[0]["dimensions"] == 512
    assert fake.embedding_calls[0]["model"] == "text-embedding-3-small"


async def test_a_short_embedding_result_raises(tmp_path: Path) -> None:
    """Positional alignment is the caller's only way to match a vector to its sentence."""

    class ShortEmbeddings(FakeOpenAI):
        class _Embeddings(FakeOpenAI._Embeddings):
            async def create(self, **kwargs):
                response = await super().create(**kwargs)
                response.data = response.data[:-1]
                return response

    fake = ShortEmbeddings()
    fake.embeddings = ShortEmbeddings._Embeddings(fake)
    with pytest.raises(Exception, match="alignment"):
        await _client(tmp_path, fake=fake).embed(["a", "b"])


async def test_embedding_nothing_calls_nothing(tmp_path: Path) -> None:
    fake = FakeOpenAI()
    assert await _client(tmp_path, fake=fake).embed([]) == []
    assert fake.embedding_calls == []


async def test_embeddings_replay(tmp_path: Path) -> None:
    recorded = _client(tmp_path, mode="record")
    first = await recorded.embed(["hello"])
    replayed = _client(tmp_path, mode="replay")
    assert await replayed.embed(["hello"]) == first


# ---------------------------------------------------------------- token budget


def test_the_budget_raises_rather_than_truncating() -> None:
    """ADR-015. A review that quietly dropped half a paper reports fewer findings."""
    budget = TokenBudget(limit=100)
    budget.charge("doc_a", 60)
    assert budget.spent("doc_a") == 60
    with pytest.raises(TokenBudgetExceeded, match="Nothing is truncated"):
        budget.charge("doc_a", 50)


def test_the_budget_is_per_document() -> None:
    budget = TokenBudget(limit=100)
    budget.charge("doc_a", 90)
    budget.charge("doc_b", 90)  # a different document has its own ceiling
    assert budget.spent("doc_b") == 90


async def test_completion_charges_the_budget(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.budget = TokenBudget(limit=50)
    await client.complete(LLMRole.REPAIR, "p", SCHEMA, doc_id="doc_x")
    assert client.budget.spent("doc_x") == 42
    with pytest.raises(TokenBudgetExceeded):
        await client.complete(LLMRole.REPAIR, "p2", SCHEMA, doc_id="doc_x")


async def test_recordings_are_json_and_human_readable(tmp_path: Path) -> None:
    """A recording nobody can read is a fixture nobody can review."""
    await _client(tmp_path, mode="record").complete(LLMRole.PLAN, "prompt", SCHEMA, system="sys")
    path = next(tmp_path.glob("*.json"))
    record = json.loads(path.read_text())
    assert record["role"] == "plan"
    assert record["model"] == "gpt-5.5"
    assert record["prompt"] == "prompt"
    assert record["system"] == "sys"
    assert record["response"] == {"title": "ok"}
