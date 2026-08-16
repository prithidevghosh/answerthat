"""Deterministic stand-ins for the model and for B2's services.

Nothing here talks to a network. The point of ADR-007's architecture is that the parts
that matter are testable without a model in the loop; these fakes cover the parts that
aren't.
"""

from __future__ import annotations

import hashlib
import re

from app.core.contracts import (
    AbstractSource,
    Claim,
    Verification,
    VerificationLabel,
)

_DIMENSIONS = 64
_TOKEN = re.compile(r"[a-z0-9]+")


class BagOfWordsEmbedder:
    """Hashed bag-of-words. Not clever, but it has the one property the reattachment tests
    need: sentences that share vocabulary score high, and sentences that don't score low."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * _DIMENSIONS
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            vector[digest[0] % _DIMENSIONS] += 1.0
        return vector


class FakeFingerprintStore:
    """B1's `anchor_fingerprints` side table (ADR-017), in a dict.

    Records `puts` and `lookups` so a test can assert the thing that matters about this
    table: that the vector went *there* and not into the IR, and that an anchor which
    already has a fingerprint reuses it instead of being re-embedded.
    """

    def __init__(self) -> None:
        self.rows: dict[str, tuple[list[float], str]] = {}
        self.puts: list[str] = []
        self.lookups: list[list[str]] = []
        self._next = 0

    async def put(self, *, vector: list[float], text: str) -> str:
        self._next += 1
        fingerprint_id = f"fp-{self._next}"
        self.rows[fingerprint_id] = (list(vector), text)
        self.puts.append(fingerprint_id)
        return fingerprint_id

    async def get_many(self, fingerprint_ids: list[str]) -> dict[str, list[float]]:
        self.lookups.append(list(fingerprint_ids))
        return {fid: self.rows[fid][0] for fid in fingerprint_ids if fid in self.rows}


class ScriptedTextModel:
    """Returns whatever it was told to return, and records what it was shown — which is how
    we assert that a citation marker never reached it."""

    def __init__(self, output: str | list[str]) -> None:
        self._outputs = [output] if isinstance(output, str) else list(output)
        self.calls: list[dict] = []

    async def rewrite(self, *, system: str, instruction: str, text: str) -> str:
        self.calls.append({"system": system, "instruction": instruction, "text": text})
        if not self._outputs:
            return ""
        return self._outputs.pop(0) if len(self._outputs) > 1 else self._outputs[0]

    @property
    def texts_seen(self) -> list[str]:
        return [call["text"] for call in self.calls]


class ScriptedRetrieval:
    """B2's retrieval. Returns source_ids that already exist in the fake store — because
    that is the only kind of id retrieval can ever return (HR-1)."""

    def __init__(self, by_claim: dict[str, list[str]] | None = None, default: list[str] | None = None):
        self._by_claim = by_claim or {}
        self._default = default or []
        self.calls: list[str] = []

    async def find_candidates(self, claim: Claim, limit: int = 10) -> list[str]:
        self.calls.append(claim.claim_id)
        return self._by_claim.get(claim.claim_id, self._default)[:limit]


class ScriptedVerifier:
    def __init__(self, by_source: dict[str, VerificationLabel] | None = None,
                 default: VerificationLabel = VerificationLabel.SUPPORTS) -> None:
        self._by_source = by_source or {}
        self._default = default
        self.calls: list[tuple[str, str]] = []

    async def verify(self, claim_text: str, source_id: str) -> Verification:
        self.calls.append((claim_text, source_id))
        label = self._by_source.get(source_id, self._default)
        return Verification(
            label=label,
            quote=None if label is VerificationLabel.UNVERIFIABLE_NO_ABSTRACT else "quoted evidence",
            abstract_source=(
                AbstractSource.UNAVAILABLE
                if label is VerificationLabel.UNVERIFIABLE_NO_ABSTRACT
                else AbstractSource.S2
            ),
            confidence=0.88,
        )


class ScriptedClaims:
    def __init__(self, claims: list[Claim]) -> None:
        self._claims = claims

    async def extract(self, document, target_ids: list[str]) -> list[Claim]:  # noqa: ANN001
        return list(self._claims)


class ScriptedPlanner:
    """A StructuredModel that returns pre-baked plan dicts, one per call."""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def plan(self, *, system: str, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("ScriptedPlanner ran out of responses")
        return self._responses.pop(0)


class InMemoryDocumentStore:
    """B1's copy-on-write store, in a dict — including its optimistic lock.

    `put_version` raises `IRVersionConflict` on a stale `parent_version` exactly as B1's
    does (ADR-021). Faking that away would let a lost-update bug pass here and lose a
    user's edit in production, which is the one failure mode this store exists to prevent.
    """

    def __init__(self) -> None:
        self._versions: dict[str, dict[int, object]] = {}

    async def get(self, doc_id: str, version: int | None = None):
        versions = self._versions.get(doc_id, {})
        if not versions:
            return None
        chosen = max(versions) if version is None else version
        stored = versions.get(chosen)
        return stored.model_copy(deep=True) if stored is not None else None

    async def put_version(self, document, *, parent_version: int):  # noqa: ANN001
        from app.core.errors import IRVersionConflict

        versions = self._versions.setdefault(document.doc_id, {})
        if versions and parent_version != max(versions):
            raise IRVersionConflict(
                f"document {document.doc_id!r} is at version {max(versions)}, but the change was "
                f"computed against version {parent_version}"
            )
        next_version = (max(versions) + 1) if versions else document.version
        stored = document.model_copy(deep=True, update={"version": next_version})
        versions[next_version] = stored
        return stored.model_copy(deep=True)

    async def list_versions(self, doc_id: str) -> list[int]:
        return sorted(self._versions.get(doc_id, {}))

    def seed(self, document) -> None:  # noqa: ANN001
        self._versions.setdefault(document.doc_id, {})[document.version] = document.model_copy(deep=True)


__all__ = [
    "BagOfWordsEmbedder",
    "FakeFingerprintStore",
    "InMemoryDocumentStore",
    "ScriptedClaims",
    "ScriptedPlanner",
    "ScriptedRetrieval",
    "ScriptedTextModel",
    "ScriptedVerifier",
]
