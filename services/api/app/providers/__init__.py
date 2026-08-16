"""Provider adapters. The only code in this system permitted to write to `source_store`.

Everything here exists to make two guarantees mechanical rather than aspirational:

* **HR-2** — an adapter whose provider *degrades silently* under anonymous limits raises
  `MissingAPIKeyError` at construction if its credential is absent: OpenAlex (key and
  polite-pool contact) and Crossref (contact). No anonymous path, no degraded mode.
  Semantic Scholar is the exception and only because it fails loudly — it answers a
  throttled anonymous call with a 429 that `http.py` raises, so its key is optional
  (`keys.py`, ADR-010 as amended by ADR-010a).
* **HR-1** — a `SourceRecord` can only enter the store carrying provenance minted from a
  real HTTP response, written by a module inside this package (`source_store.py`).

Import an adapter, not its internals: `SemanticScholarProvider`, `OpenAlexProvider`,
`CrossrefProvider` implement the `Provider` protocol from Appendix A.
"""
