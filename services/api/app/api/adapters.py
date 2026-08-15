"""Adapters from other agents' real signatures onto `app/agent/ports.py`.

The impedance mismatches are small and entirely expected — B1's document store speaks
`head`/`commit`/`history`, my `VersionService` speaks `get`/`put_version`/`list_versions` —
and translating them here rather than reaching into either side is what keeps `app/agent/`
importing nothing from `app/ir/`, `app/export/`, `app/providers/` or `app/review/`.

These adapters are the only place in B3's code that knows another package's shape.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.contracts import Document

log = logging.getLogger("app.api.adapters")


class DocumentStoreAdapter:
    """B1's `app.ir.store.DocumentStore` → B3's `DocumentStore` port.

    B1's `get` requires an explicit version and `head` returns the latest; B3's port
    treats `version=None` as "latest". The translation is here so neither side has to
    care about the other's convention.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    async def get(self, doc_id: str, version: int | None = None) -> Document | None:
        if version is None:
            return await self._store.head(doc_id)
        return await self._store.get(doc_id, version)

    async def put_version(self, document: Document, *, parent_version: int) -> Document:
        return await self._store.commit(document, parent_version=parent_version, label="agent-edit")

    async def list_versions(self, doc_id: str) -> list[int]:
        return [info.version for info in await self._store.history(doc_id)]


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
            )
        except ExportFailure as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001 — reported as a reason, never swallowed
            log.exception("render probe failed for %s", document.doc_id)
            return False, f"{type(exc).__name__}: {exc}"
        return True, None


class LatexExporter:
    """B1's `export_latex` → B3's `Exporter` port."""

    def __init__(self, csl_lookup: Any, styles_dir: Path | None = None) -> None:
        self._sources = csl_lookup
        self._styles_dir = styles_dir

    async def to_latex(self, document: Document) -> str:
        from app.export.latex import export_latex  # noqa: PLC0415

        return export_latex(
            document, self._sources(document), styles_dir=self._styles_dir
        ).latex


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
    "DocumentStoreAdapter",
    "LatexExporter",
    "PandocRenderProbe",
    "SourceReaderAdapter",
    "csl_lookup_for",
]
