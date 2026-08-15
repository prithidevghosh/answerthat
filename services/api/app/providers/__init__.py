"""Provider adapters. The only code in this system permitted to write to `source_store`.

Everything here exists to make two guarantees mechanical rather than aspirational:

* **HR-2** — every adapter raises `MissingAPIKeyError` at construction if its key is
  absent. There is no anonymous path and no degraded mode (`keys.py`, ADR-010).
* **HR-1** — a `SourceRecord` can only enter the store carrying provenance minted from a
  real HTTP response, written by a module inside this package (`source_store.py`).

Import an adapter, not its internals: `SemanticScholarProvider`, `OpenAlexProvider`,
`CrossrefProvider` implement the `Provider` protocol from Appendix A.
"""
