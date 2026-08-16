"""The evidence index. ADR-034.

Structured lookup answers most of what the agent is asked. "How many references
resolved?" is `get_parse_report`; "what does finding fnd_abc say?" is `get_finding`. What
structured lookup cannot answer is the question a researcher actually asks: *"which part
of my paper does this finding attack?"*, *"what else is in that reference?"*, *"why did
you flag that?"*. Those need similarity over text, and the agent has no way to guess a
`span_id` from a description of what the sentence says.

So: one embedding per span, abstract, claim and finding, cosine similarity in Python over
the rows belonging to **one document**.

Three decisions worth defending.

**No pgvector, no vector service, no graph store.** A paper is a few hundred spans and a
few hundred abstracts. A 512-float dot product over 800 rows is a fraction of a
millisecond, and the entire corpus for a query is one document's rows. Adding an index
type, an extension or a service to make a sub-millisecond scan faster would be cost with
no benefit and a new failure mode. If a multi-document corpus ever exists — searching
across every paper a lab has uploaded — pgvector is the scale path, and the table below
is already shaped for it: swapping `JSONB` for `vector(512)` and adding an ivfflat index
is the whole migration.

**One embedding model, the one in `settings.embedding_model` at
`settings.embedding_dimensions` (ADR-016).** A second model would produce vectors that
score plausibly against the first model's and mean nothing — cosine similarity between
two embedding spaces is not an error, it is noise with a number attached.

**The build has a status, and search reports it.** An index still building returns fewer
hits, and fewer hits is indistinguishable from a thorough search of a paper that does not
discuss the topic. So `search()` says which kinds have been indexed and whether the build
is still running, and the agent is told to pass that on. Silently returning a short list
is the same false negative as ADR-010, one layer up.

Rows are insert-only, mirroring `app/ir/fingerprints.py`: re-indexing a document writes
new rows rather than mutating old ones, and a row is identified by `(doc_id, kind,
ref_id)` so a second build is a no-op rather than a duplicate.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy import Integer, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow
from app.ir import ids
from app.orchestrator.ports import Embedder

log = logging.getLogger("app.orchestrator.index")

__all__ = [
    "EmbeddingRow",
    "EvidenceIndex",
    "EvidenceRowStore",
    "IndexEntry",
    "IndexStatus",
    "InMemoryEvidenceRowStore",
    "PostgresEvidenceRowStore",
    "SearchHit",
    "EvidenceKind",
]

EvidenceKind = Literal["span", "abstract", "claim", "finding"]

#: Every kind the index can hold, in the order a document acquires them: spans when the
#: parse completes, then abstracts and claims and findings as the review produces them.
ALL_KINDS: tuple[EvidenceKind, ...] = ("span", "abstract", "claim", "finding")


class EmbeddingRow(Base):
    """`doc_embeddings` — insert-only, one row per indexed piece of text."""

    __tablename__ = "doc_embeddings"

    embedding_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    ref_id: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    vector: Mapped[list] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(64))
    dimensions: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text)


@dataclass(frozen=True)
class IndexEntry:
    """One row, in memory."""

    doc_id: str
    kind: EvidenceKind
    ref_id: str
    text: str
    vector: list[float]


@dataclass(frozen=True)
class SearchHit:
    kind: EvidenceKind
    ref_id: str
    text: str
    score: float


@dataclass
class IndexStatus:
    """What the index currently knows about one document.

    Returned with every search so a short result set can be read correctly. `building`
    plus `kinds_indexed: ["span"]` means "the paper's text is searchable, the review's
    output is not yet" — a real answer, and a different one from "nothing matched".
    """

    doc_id: str
    state: Literal["empty", "building", "ready", "failed"] = "empty"
    kinds_indexed: list[str] = field(default_factory=list)
    rows: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "kinds_indexed": sorted(self.kinds_indexed),
            "kinds_missing": sorted(set(ALL_KINDS) - set(self.kinds_indexed)),
            "rows": self.rows,
            "error": self.error,
        }


class EvidenceRowStore(Protocol):
    async def put_many(self, entries: list[IndexEntry], *, model: str, dimensions: int) -> int: ...
    async def rows_for(self, doc_id: str) -> list[IndexEntry]: ...
    async def existing_ref_ids(self, doc_id: str, kind: str) -> set[str]: ...


class PostgresEvidenceRowStore:
    def __init__(self, session_scope: Any) -> None:
        self._session_scope = session_scope

    async def put_many(self, entries: list[IndexEntry], *, model: str, dimensions: int) -> int:
        if not entries:
            return 0
        async with self._session_scope() as session:
            written = 0
            for entry in entries:
                session.add(
                    EmbeddingRow(
                        embedding_id=ids.new_id("emb"),
                        doc_id=entry.doc_id,
                        kind=entry.kind,
                        ref_id=entry.ref_id,
                        text=entry.text,
                        vector=list(entry.vector),
                        model=model,
                        dimensions=dimensions,
                        created_at=utcnow().isoformat(),
                    )
                )
                written += 1
            await session.flush()
            return written

    async def rows_for(self, doc_id: str) -> list[IndexEntry]:
        async with self._session_scope() as session:
            return await self._rows_for(session, doc_id)

    @staticmethod
    async def _rows_for(session: AsyncSession, doc_id: str) -> list[IndexEntry]:
        stmt = select(EmbeddingRow).where(EmbeddingRow.doc_id == doc_id)
        rows = (await session.scalars(stmt)).all()
        return [
            IndexEntry(
                doc_id=row.doc_id,
                kind=row.kind,  # type: ignore[arg-type]
                ref_id=row.ref_id,
                text=row.text,
                vector=list(row.vector),
            )
            for row in rows
        ]

    async def existing_ref_ids(self, doc_id: str, kind: str) -> set[str]:
        async with self._session_scope() as session:
            stmt = select(EmbeddingRow.ref_id).where(
                EmbeddingRow.doc_id == doc_id, EmbeddingRow.kind == kind
            )
            return set((await session.scalars(stmt)).all())


class InMemoryEvidenceRowStore:
    """Process-local rows, for tests and for anything running without Postgres."""

    def __init__(self) -> None:
        self._rows: list[IndexEntry] = []

    async def put_many(self, entries: list[IndexEntry], *, model: str, dimensions: int) -> int:
        self._rows.extend(entries)
        return len(entries)

    async def rows_for(self, doc_id: str) -> list[IndexEntry]:
        return [row for row in self._rows if row.doc_id == doc_id]

    async def existing_ref_ids(self, doc_id: str, kind: str) -> set[str]:
        return {row.ref_id for row in self._rows if row.doc_id == doc_id and row.kind == kind}


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity, 0.0 for mismatched widths.

    Mismatched widths mean one of the vectors was written under a different embedding
    configuration. Scoring it as 0.0 rather than raising keeps one stale row from taking
    down a search, and it can never *win*, so the worst case is a hit that is missing
    rather than a hit that is wrong.
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class EvidenceIndex:
    """Builds and searches the per-document evidence index."""

    def __init__(
        self,
        *,
        rows: EvidenceRowStore,
        embedder: Embedder,
        model: str,
        dimensions: int,
        batch_size: int,
        text_chars: int,
    ) -> None:
        self._rows = rows
        self._embedder = embedder
        self._model = model
        self._dimensions = dimensions
        self._batch = batch_size
        self._text_chars = text_chars
        self._status: dict[str, IndexStatus] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ status

    def status(self, doc_id: str) -> IndexStatus:
        return self._status.get(doc_id) or IndexStatus(doc_id=doc_id)

    def _lock_for(self, doc_id: str) -> asyncio.Lock:
        lock = self._locks.get(doc_id)
        if lock is None:
            lock = self._locks[doc_id] = asyncio.Lock()
        return lock

    # ------------------------------------------------------------------ building

    def schedule(self, doc_id: str, texts: list[tuple[EvidenceKind, str, str]]) -> None:
        """Index in the background. Never blocks the caller.

        Called from the watcher when a parse or a review completes, so a build must not
        delay the notice that told the agent the work finished. A failure lands on the
        status and is reported by `search`, not raised into the caller's turn.
        """
        task = asyncio.create_task(self._build_guarded(doc_id, texts))
        # Held for the same reason the ingest pipeline holds its tasks: a task nothing
        # references can be collected mid-flight, and the build would vanish silently.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _build_guarded(self, doc_id: str, texts: list[tuple[EvidenceKind, str, str]]) -> None:
        try:
            await self.build(doc_id, texts)
        except Exception as exc:  # noqa: BLE001 — recorded on the status, then reported
            log.exception("evidence index build failed for %s", doc_id)
            status = self._status.setdefault(doc_id, IndexStatus(doc_id=doc_id))
            status.state = "failed"
            status.error = f"{type(exc).__name__}: {exc}"

    async def build(self, doc_id: str, texts: list[tuple[EvidenceKind, str, str]]) -> int:
        """Embed and store `(kind, ref_id, text)` triples. Returns rows written.

        Already-indexed `ref_id`s are skipped rather than re-embedded: the index is
        additive, review output arrives in waves, and re-embedding a paper's spans every
        time a finding lands would multiply the embedding bill by the number of waves.
        """
        async with self._lock_for(doc_id):
            status = self._status.setdefault(doc_id, IndexStatus(doc_id=doc_id))
            status.state = "building"

            pending: list[tuple[EvidenceKind, str, str]] = []
            for kind in ALL_KINDS:
                incoming = [t for t in texts if t[0] == kind]
                if not incoming:
                    continue
                known = await self._rows.existing_ref_ids(doc_id, kind)
                pending.extend(t for t in incoming if t[1] not in known and t[2].strip())

            written = 0
            for start in range(0, len(pending), self._batch):
                chunk = pending[start : start + self._batch]
                vectors = await self._embedder.embed(
                    [text[: self._text_chars] for _kind, _ref, text in chunk]
                )
                if len(vectors) != len(chunk):
                    raise RuntimeError(
                        f"asked for {len(chunk)} embeddings and got {len(vectors)}. Position "
                        "is the only thing tying a vector to its text, so a short result "
                        "cannot be used."
                    )
                written += await self._rows.put_many(
                    [
                        IndexEntry(
                            doc_id=doc_id,
                            kind=kind,
                            ref_id=ref_id,
                            text=text[: self._text_chars],
                            vector=vector,
                        )
                        for (kind, ref_id, text), vector in zip(chunk, vectors, strict=True)
                    ],
                    model=self._model,
                    dimensions=self._dimensions,
                )

            rows = await self._rows.rows_for(doc_id)
            status.rows = len(rows)
            status.kinds_indexed = sorted({row.kind for row in rows})
            status.state = "ready" if rows else "empty"
            status.error = None
            log.info(
                "evidence index for %s: %d row(s) across %s (+%d this pass)",
                doc_id,
                status.rows,
                status.kinds_indexed or "nothing",
                written,
            )
            return written

    # ------------------------------------------------------------------ search

    async def search(
        self,
        doc_id: str,
        query: str,
        *,
        k: int,
        kinds: list[str] | None = None,
    ) -> tuple[list[SearchHit], dict[str, Any]]:
        """Cosine search over one document's rows.

        Returns `(hits, status)`. The status always travels with the hits: a caller that
        can see three results and cannot see that the index holds only spans has no way
        to tell a thin index from a thin paper.
        """
        status = self.status(doc_id)
        rows = await self._rows.rows_for(doc_id)
        if rows:
            # The status may be `empty` in a fresh process that never built this index
            # but whose rows are still in Postgres from before a restart. The rows are the
            # authority; the in-memory status is a cache of the last build this process ran.
            status = IndexStatus(
                doc_id=doc_id,
                state="ready" if status.state != "building" else "building",
                kinds_indexed=sorted({row.kind for row in rows}),
                rows=len(rows),
                error=status.error,
            )
            self._status[doc_id] = status

        wanted = set(kinds or ALL_KINDS)
        candidates = [row for row in rows if row.kind in wanted]
        if not candidates or not query.strip():
            return [], status.as_dict()

        vectors = await self._embedder.embed([query])
        if not vectors:
            raise RuntimeError("the embedder returned no vector for the search query")
        probe = vectors[0]

        scored = [
            SearchHit(kind=row.kind, ref_id=row.ref_id, text=row.text, score=cosine(probe, row.vector))
            for row in candidates
        ]
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[: max(1, k)], status.as_dict()
