"""Adapters from other agents' real signatures onto `app/agent/ports.py`.

The impedance mismatches are small and entirely expected — B1's document store speaks
`head`/`commit`/`history`, my `VersionService` speaks `get`/`put_version`/`list_versions` —
and translating them here rather than reaching into either side is what keeps `app/agent/`
importing nothing from `app/ir/`, `app/export/`, `app/providers/` or `app/review/`.

These adapters are the only place in B3's code that knows another package's shape.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any

from app.core.contracts import Document

log = logging.getLogger("app.api.adapters")


async def maybe_await(value: Any) -> Any:
    """Await `value` if it is awaitable, otherwise return it.

    B1's ingest pipeline and B2's review runner start their background work with
    `asyncio.create_task` and so expose `enqueue`/`start`/`status` as *synchronous*
    methods, while B3's ports declare them async. Rather than guess which side is right
    and break at runtime on the wrong guess, the routes accept either.

    This is an interop shim, not error handling: nothing is swallowed here and no default
    is substituted. When both sides settle on one shape, delete it.
    """
    return await value if inspect.isawaitable(value) else value


class DocumentStoreAdapter:
    """B1's `app.ir.store.DocumentStore` → B3's `DocumentStore` port.

    Two translations. The obvious one: B1's `get` requires an explicit version and `head`
    returns the latest, where B3's port treats `version=None` as "latest".

    The less obvious one, and the reason this holds a *factory* rather than a store: B1's
    `PostgresDocumentStore` takes an `AsyncSession`, and a session is per-unit-of-work, not
    per-process. Holding one open for the lifetime of the app would accumulate a
    transaction across unrelated requests and make one bad edit poison the next. Each call
    opens its own `session_scope()`.
    """

    def __init__(self, store_factory: Any, session_scope: Any) -> None:
        self._store_for = store_factory
        self._session_scope = session_scope

    async def get(self, doc_id: str, version: int | None = None) -> Document | None:
        async with self._session_scope() as session:
            store = self._store_for(session)
            if version is None:
                return await store.head(doc_id)
            return await store.get(doc_id, version)

    async def put_version(self, document: Document, *, parent_version: int) -> Document:
        async with self._session_scope() as session:
            return await self._store_for(session).commit(
                document, parent_version=parent_version, label="agent-edit"
            )

    async def list_versions(self, doc_id: str) -> list[int]:
        async with self._session_scope() as session:
            return [info.version for info in await self._store_for(session).history(doc_id)]


class FingerprintStoreAdapter:
    """B1's `app.ir.fingerprints` side table → B3's `FingerprintStore` port (ADR-017).

    Two translations. B1's store speaks in `Fingerprint` records and expects the caller to
    mint the id; B3's port speaks in vectors and gets an id back. And, as with the document
    store, this holds a *factory*: `PostgresFingerprintStore` takes an `AsyncSession`, and
    a session is per-unit-of-work rather than per-process.

    The dimension check is here rather than in the transform because this is the only
    place that knows what the configured embedding width is. It matters more than it looks:
    cosine similarity between vectors of different lengths is defined as 0.0 by
    `transform.cosine`, so a stale 256-dimension row would not error — it would surface
    every anchor in the paragraph as a user decision and look like a bad rewrite (HR-3).
    """

    def __init__(self, store_factory: Any, session_scope: Any, *, model: str, dimensions: int) -> None:
        self._store_for = store_factory
        self._session_scope = session_scope
        self._model = model
        self._dimensions = dimensions

    async def put(self, *, vector: list[float], text: str) -> str:
        from app.ir.fingerprints import Fingerprint, new_fingerprint_id  # noqa: PLC0415

        if len(vector) != self._dimensions:
            raise ValueError(
                f"refusing to store a {len(vector)}-dimension fingerprint when the configured "
                f"embedding width is {self._dimensions}. A mismatched vector scores 0.0 against "
                f"everything and would surface every anchor as a user decision rather than fail."
            )
        record = Fingerprint(
            fingerprint_id=new_fingerprint_id(),
            vector=list(vector),
            text=text,
            model=self._model,
            dimensions=self._dimensions,
        )
        async with self._session_scope() as session:
            return await self._store_for(session).put(record)

    async def get_many(self, fingerprint_ids: list[str]) -> dict[str, list[float]]:
        ids = [fid for fid in dict.fromkeys(fingerprint_ids) if fid]
        if not ids:
            return {}
        async with self._session_scope() as session:
            found = await self._store_for(session).get_many(ids)
        return {
            fid: list(record.vector)
            for fid, record in found.items()
            # A row written at a different width is treated as absent, so the caller
            # re-embeds from the sentence it still has rather than matching against noise.
            if len(record.vector) == self._dimensions
        }


class SourceReaderAdapter:
    """B2's source store → B3's `SourceReader` port.

    B2's sync `get`/`has` answer from an in-process index, and an id that was never warmed
    **raises `SourceNotIndexed`** rather than reporting absence — deliberately, because "we
    never looked" reported as "does not exist" would be a false kernel REJECT
    indistinguishable from a real one (memory.md §5, B2 → B3).

    So warming is mandatory before any `has()` call, and it happens outside the kernel:
    the kernel is pure and synchronous and is never given a reason to await anything.
    `warm()` is called by the command loop and by the version service with exactly the ids
    the kernel is about to check — fabricated ones included, which is the point. Those come
    back known-absent and `has()` returns False, which is the REJECT we want.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    async def warm(self, source_ids) -> None:  # noqa: ANN001
        ids = [sid for sid in dict.fromkeys(source_ids) if sid]
        if ids:
            await self._store.warm(ids)

    def get(self, source_id: str):
        return self._store.get(source_id)

    def has(self, source_id: str) -> bool:
        return self._store.has(source_id)


#: The style the probe renders in when the document has none yet.
#:
#: Rule 5 asks "does this document still render", not "how should it look". The probe
#: suppresses the bibliography and throws its output away — nothing it produces is shown
#: to anyone or written anywhere — so a fixed style here substitutes nothing. Falling back
#: to `_resolve_style`'s refusal instead meant every edit to a paper whose style is still
#: ambiguous (ADR-011 leaves that to the user, and most papers arrive that way) came back
#: `pandoc_refused: no citation style selected` — a verdict about the *edit*, naming
#: Pandoc, for a choice the user had not been asked to make yet. Export still refuses:
#: there the style is the answer, and guessing it would be the silent substitution HR-3
#: forbids.
PROBE_STYLE = "ieee"


class PandocRenderProbe:
    """B1's LaTeX exporter → B3's `RenderProbe` port (kernel REJECT rule 5).

    The probe must never raise to mean "won't render": the kernel needs the reason as a
    string so it can hand it back to the planner. An `ExportFailure` here is a legitimate
    verdict about the document, not a fault in this service.
    """

    def __init__(self, csl_lookup: Any, styles_dir: Path | None = None) -> None:
        self._sources = csl_lookup
        self._styles_dir = styles_dir

    def can_render(self, document: Document) -> tuple[bool, str | None]:
        from app.core.errors import ExportFailure  # noqa: PLC0415
        from app.export.latex import export_latex  # noqa: PLC0415

        try:
            export_latex(
                document,
                self._sources(document),
                styles_dir=self._styles_dir,
                suppress_bibliography=True,
                style_id=document.metadata.style_id or PROBE_STYLE,
            )
        except ExportFailure as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001 — reported as a reason, never swallowed
            log.exception("render probe failed for %s", document.doc_id)
            return False, f"{type(exc).__name__}: {exc}"
        return True, None


class LatexExporter:
    """B1's `export_latex` → B3's `Exporter` port.

    Holds the reader as well as the lookup because it is the one caller that has to warm
    it. The lookup reads through B2's *sync* accessors, which raise `SourceNotIndexed` for
    an id nobody warmed (see `SourceReaderAdapter` above) — and unlike the command loop,
    which warms the ids the kernel is about to check, nothing on the export path had done
    it. So every export raised `SourceNotIndexed` out of the route as a bare 500 whenever
    the in-process index was cold, which after any API restart it always is. `to_latex` is
    already async precisely so it can await something like this.
    """

    def __init__(
        self, csl_lookup: Any, styles_dir: Path | None = None, reader: Any = None
    ) -> None:
        self._sources = csl_lookup
        self._styles_dir = styles_dir
        self._reader = reader

    async def to_latex(self, document: Document) -> str:
        from app.export.latex import export_latex  # noqa: PLC0415
        from app.ir.traversal import source_id_multiset  # noqa: PLC0415

        if self._reader is not None:
            await self._reader.warm(sorted(source_id_multiset(document)))

        return export_latex(
            document, self._sources(document), styles_dir=self._styles_dir
        ).latex


# --------------------------------------------------------------------------- orchestrator
#
# The orchestrator's ports are satisfied by the same collaborators the deterministic
# screens use, reshaped. Nothing below re-implements a decision: `CommandGateway` runs the
# same command loop `POST /commands` runs, `VersionGateway` runs the same commit
# `POST /approve` runs, and `ExportGateway` reads the same manifest the export screen
# reads. If the chat and a screen could ever disagree about what an operation does, one of
# them would be lying to the user, and which one would depend on where they happened to be
# looking.


class CommandGatewayAdapter:
    """`app/agent/`'s command loop and change-set store → `CommandGateway`.

    Returns dicts rather than `ProposedChangeSet`, because `app/orchestrator/` must not
    import `app/agent/`. The dump is not a lossy summary — it is the same payload the
    edit console renders, so the chat can show the real diff and the real citation ledger.
    """

    def __init__(self, services: Any) -> None:
        self._services = services

    async def propose(self, document: Document, instruction: str) -> dict:
        change_set = await self._services.command_loop().run(document, instruction)
        self._services.change_set_store().put(change_set)
        return change_set.model_dump(mode="json")

    def get_change_set(self, change_set_id: str) -> dict:
        return self._services.change_set_store().get(change_set_id).model_dump(mode="json")


class VersionGatewayAdapter:
    """`app/agent/versioning.py` → `VersionGateway`.

    The one path from the chat to a written document version. It builds the
    `ApprovalRequest` from the *stored* change set and the caller's approvals, and it lets
    `VersionConflict`, `ApprovalError` and `KernelRejection` propagate — the orchestrator
    turns each into a distinct refusal the agent explains, and swallowing any of them here
    would collapse three different answers into one.
    """

    def __init__(self, services: Any) -> None:
        self._services = services

    async def commit(
        self,
        change_set_id: str,
        *,
        base_version: int,
        approved_change_ids: list[str],
        rejected_change_ids: list[str],
        orphan_decisions: list[dict],
    ) -> dict:
        from app.agent.versioning import ApprovalRequest, OrphanDecision  # noqa: PLC0415

        store = self._services.change_set_store()
        change_set = store.get(change_set_id)
        result = await self._services.versions().commit(
            change_set,
            ApprovalRequest(
                change_set_id=change_set_id,
                base_version=base_version,
                approved_change_ids=approved_change_ids,
                rejected_change_ids=rejected_change_ids,
                orphan_decisions=[
                    OrphanDecision.model_validate(decision) for decision in orphan_decisions
                ],
            ),
        )
        if result.committed:
            store.discard(change_set_id)
        return result.model_dump(mode="json")

    async def revert(self, doc_id: str, to_version: int) -> dict:
        result = await self._services.versions().revert(doc_id, to_version)
        return result.model_dump(mode="json")

    async def set_style(self, document: Document) -> int:
        stored = await self._services.require("documents").put_version(
            document, parent_version=document.version
        )
        return stored.version


class ExportGatewayAdapter:
    """B1's exporter and the export manifest → `ExportGateway`.

    `download_url` points at the existing `GET /export.tex` route rather than at bytes
    embedded in a chat message. The file the agent hands over and the file the export
    screen hands over are then the same file from the same renderer, and the browser
    downloads it the way it downloads everything else.
    """

    def __init__(self, services: Any) -> None:
        self._services = services

    async def _document(self, doc_id: str, version: int | None) -> Document:
        document = await self._services.require("documents").get(doc_id, version)
        if document is None:
            raise KeyError(
                f"document {doc_id!r}"
                + (f" version {version}" if version is not None else "")
                + " does not exist"
            )
        return document

    async def manifest(self, doc_id: str, version: int | None = None) -> dict:
        from app.api.routes.documents import build_export_manifest  # noqa: PLC0415

        document = await self._document(doc_id, version)
        return build_export_manifest(doc_id, document).model_dump(mode="json")

    async def to_latex(self, doc_id: str, version: int | None = None) -> dict:
        document = await self._document(doc_id, version)
        latex = await self._services.require("exporter").to_latex(document)
        query = f"?version={document.version}"
        return {
            "filename": f"{doc_id}-v{document.version}.tex",
            "byte_size": len(latex.encode("utf-8")),
            "download_url": f"/api/documents/{doc_id}/export.tex{query}",
            "version": document.version,
            "style_id": document.metadata.style_id,
            "style_uncertain": bool(document.metadata.style_id)
            and document.metadata.style_ambiguous,
        }


class RetrievalIntrospectorAdapter:
    """B2's `CandidateGenerator` → `RetrievalIntrospector`.

    This is the adapter that makes `describe_review_plan` honest. It asks the generator
    which strategies will run for *this* document, using the generator's own rule, rather
    than restating that rule here. Restating it is exactly how `retrieval.py` once
    reported an unauthenticated `s2_snippet` as having run: a second copy of a rule goes
    wrong the moment the first one grows a case the copy does not know about.
    """

    def __init__(self, retrieval: Any) -> None:
        self._retrieval = retrieval

    async def strategies_for(self, doc_id: str) -> tuple[list[str], list[str]]:
        generator = self._retrieval.generator
        context = await self._retrieval.prime(doc_id)
        will_run = list(generator.strategies_for(context))
        return will_run, [s for s in generator.ALL_STRATEGIES if s not in will_run]


def csl_lookup_for(source_reader: Any):
    """Build the `source_id → CSL-JSON` mapping B1's exporter wants, from B2's store.

    Read-only, by construction: `SourceReader` has no `put` (HR-1).
    """
    from app.ir.traversal import source_id_multiset  # noqa: PLC0415

    def lookup(document: Document) -> dict[str, dict]:
        csl: dict[str, dict] = {}
        for source_id in sorted(source_id_multiset(document)):
            record = source_reader.get(source_id)
            if record is not None:
                csl[source_id] = record.csl
        return csl

    return lookup


__all__ = [
    "CommandGatewayAdapter",
    "DocumentStoreAdapter",
    "ExportGatewayAdapter",
    "FingerprintStoreAdapter",
    "LatexExporter",
    "PandocRenderProbe",
    "RetrievalIntrospectorAdapter",
    "SourceReaderAdapter",
    "VersionGatewayAdapter",
    "csl_lookup_for",
    "maybe_await",
]
