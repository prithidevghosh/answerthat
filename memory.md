# memory.md — Shared Working Memory

**Purpose.** Your context window will be cut. This file is how you and the other agents remember
things across that cut. Anything you learn that a future agent would waste time rediscovering goes
here — immediately, not at the end of your session.

**Read this at the start of every session, after `goal.md` and `decision.md`.**

**Append, don't rewrite.** Other agents' notes are not yours to delete. If a note becomes wrong,
strike it and add the correction beneath it with the date.

---

## 1. Repetitive work — do these without being asked

### Commit protocol
Commit after each coherent feature or fix. Not at the end of the session, not once per file.

```
<scope>: <imperative summary under 60 chars>

<what changed and why — 1-3 lines>
<checkpoint reference if applicable, e.g. "Advances CP-2">
```

`<scope>` is one of: `ir`, `parsing`, `export`, `providers`, `review`, `agent`, `api`, `web`,
`core`, `test`, `infra`, `docs`.

Examples:
```
parsing: add arbiter agreement scoring

Reconciles parsed references against Crossref/S2/OpenAlex, accepting
matches at >= 0.85. External record replaces our parse on accept.
Advances CP-2.
```
```
providers: raise MissingAPIKeyError at construction

Both S2 and OpenAlex adapters now fail at import time rather than
returning empty results. Enforces HR-2 / ADR-010.
```

**Never commit:** `.env`, real API keys, `node_modules/`, `__pycache__/`, downloaded PDFs, GROBID
model files, or large fixtures (use Git LFS or a fixture download script instead).

### The end-of-work checklist
Before you stop, every time:

1. Tests for what you built — passing
2. New learnings appended to §4 of this file
3. Any decision change captured as an ADR in `decision.md`
4. Checkpoint criteria in `goal.md` re-read and honestly assessed
5. Everything committed with a proper message
6. Blockers written into §5 below, with enough detail that someone else could act on them

### Before claiming a checkpoint
Re-read the acceptance criteria **as a list**, tick each one against actual evidence, and paste that
evidence (test output, command output, screenshot path) into §6. "It works" is not evidence.

---

## 2. Environment and commands

```bash
cp .env.example .env          # then fill in both keys — the app will not start without them
docker compose up -d          # api, web, grobid, postgres, redis
docker compose logs -f api

# backend
cd services/api && uv run uvicorn app.main:app --reload
cd services/api && uv run pytest tests/unit -q
cd services/api && uv run ruff check . && uv run mypy app

# frontend
cd apps/web && pnpm dev
cd apps/web && pnpm build && pnpm lint
```

Required env vars — **the app raises on startup if either is missing (HR-2 / ADR-010):**

| Var | Where to get it |
|---|---|
| `SEMANTIC_SCHOLAR_API_KEY` | free, request at semanticscholar.org/product/api |
| `OPENALEX_API_KEY` | free, register at openalex.org |
| `OPENALEX_MAILTO` | your contact email — required for the polite pool |
| `ANTHROPIC_API_KEY` | model calls (planner, claim extraction, verifier, repair tier) |
| `GROBID_URL` | defaults to `http://grobid:8070` in compose |

---

## 3. Known gotchas — read before you hit them

**OpenAlex returns `abstract_inverted_index`, not an abstract.** It's `{token: [positions]}`. You
must invert it to reconstruct the text. There is no plain-abstract field. *(B2)*

**OpenAlex is credit-metered, not request-metered.** 1 credit for a singleton, 10 for a list query,
100 for content, 1000 for vector search. A free key is 100k credits/day. Budget list queries;
keep the vector endpoint off by default. *(B2)*

**Semantic Scholar is ~1 request/second with a key.** Everything must batch and cache. Once you have
IDs, `/paper/batch` hydrates up to 500 records in one call — use it instead of looping. *(B2)*

**S2 abstracts are missing for a meaningful fraction of records** due to publisher licensing. The
fallback chain is mandatory: S2 → OpenAlex inverted → S2 TLDR → `unavailable`. `unavailable` is a
real, displayable outcome, not an error to swallow. *(B2)*

**GROBID takes ~30–60s to become healthy on first boot.** Don't treat early connection refusals as
failure; add a healthcheck and a startup wait. The image is 1–3 GB. *(B1)*

**GROBID links in-text markers to references for you.** `<ref type="bibr" target="#b12">` in the
body points at `<biblStruct xml:id="b12">` in `<listBibl>`. Do not rebuild this from text — it is
the single most valuable thing GROBID gives us and the precondition for HR-5. *(B1)*

**Ask GROBID for what you need up front:** `consolidateHeader=1`, `consolidateCitations=1`,
`teiCoordinates` for `ref`, `biblStruct`, `head`, `p`. Re-running the full parse to get coordinates
later is expensive. *(B1)*

**TEI → CSL-JSON is fiddlier than it looks.** Name particles (`van der`), `analytic` vs `monogr`
(article-in-journal vs standalone), container-title, and page ranges are the four places it goes
wrong. Write tests for those four specifically. *(B1)*

**Pandoc needs the CSL file path, not a style name.** Keep `packages/csl-styles/` mounted into the
api container and reference files by path. The frontend's `citation.js` must read the *same* files
or preview and export will drift. *(B1, F1)*

**SSE through Next.js:** don't proxy the stream through a Next.js API route — connect the browser
directly to the FastAPI SSE endpoint, or buffering will make streaming look broken. *(F1, B3)*

---

## 4. Learnings log

> Append new entries at the bottom. Format: `YYYY-MM-DD · <agent> · <one-line title>` then 1–4 lines.
> Include the thing you'd have wanted to know an hour earlier.

2026-08-15 · B1 · `app/core/` has landed and is frozen — import it, don't re-declare it
`app.core.contracts` is Appendix A **verbatim** (extracted mechanically from goal.md; a test
asserts byte equality). `app.core.errors` re-exports the four Appendix A errors — same classes,
not copies — plus `GrobidUnavailable`, `ExportFailure`, `SourceStoreViolation`, etc.
`app.core.db` gives you `Base`, `JSONB`, `session_scope()`, `get_session()` (FastAPI dep),
`utcnow()`, `create_all()`. Hang every ORM table off that `Base` or `create_all()` won't see it.

2026-08-15 · B1 · `ANTHROPIC_API_KEY` is deliberately NOT a startup-abort key
HR-2 names exactly two keys, so `Settings` requires only those. But absence of the Anthropic key
must not silently skip the repair tier / planner / verifier — that's HR-3. Whoever owns each of
those call sites raises `MissingAPIKeyError` at the point of use. Do not add an "if no key, skip"
branch.

2026-08-15 · B1 · Config carries the ADR thresholds; don't re-hardcode them
`arbiter_accept_threshold=0.85` (ADR-001), `style_ambiguity_margin=0.05` (ADR-011),
`repair_confidence_threshold=0.75` (ADR-003) live on `Settings`. A test pins the first two to
their ADR values, so changing them requires an ADR rather than an env edit.

2026-08-15 · B2 · OpenAlex `cites:` and `cited_by:` mean the opposite of what you'd guess
Verified against `ourresearch/openalex-docs`: **`cites:W123` = works that cite W123** (forward
citations); **`cited_by:W123` = works in W123's `referenced_works`** (its own bibliography).
Swapping them returns plausible-looking, entirely wrong candidates and nothing downstream can
tell. `app/providers/openalex.py` names the two methods `citing_works` / `referenced_works` so
the filter string is never written at a call site.

2026-08-15 · B2 · OpenAlex auth is a **query param**, not a header, and OR'd filters cap at 50
`?api_key=…` per the docs (keys mandatory since 2026-02-13). `mailto` goes in the query string
*and* the User-Agent — the docs accept either. Credit costs confirmed as goal.md states them:
1 singleton / 10 list / 100 content / 1000 vector, 100k/day free, plus a hard 100 rps. Pipe-OR
up to 50 values in one filter: that turns 50 one-hop expansions from 500 credits into 10.

2026-08-15 · B2 · Commas, pipes and colons in a title silently rewrite an OpenAlex filter
`filter=title.search:Attention, Memory | Recall` parses the comma as an AND clause and the pipe
as an OR value, so you get a different query and no error. `_escape_filter()` strips all three
before interpolation. Same class of bug as SQL injection, minus the security stakes.

2026-08-15 · B2 · Set the User-Agent per request, not on the `httpx.AsyncClient`
Client-level default headers vanish the moment anyone passes in their own client (tests do, and
a shared-client refactor would). For OpenAlex that silently drops the polite-pool contact, whose
only symptom is throttling that looks like sparse results. `ProviderHTTP` merges headers into
every request instead. A test caught this; it would not have shown up in production for weeks.

2026-08-15 · B2 · Cache key and request body must agree, or batching quietly re-fetches
`normalize_query` sorts containers, so `["b","a"]` and `["a","b"]` are one cache entry. That is
only safe because `batch_hydrate` also **sorts the ids it sends** and maps results back by paper
id rather than by position. If you add a batch endpoint, sort the request the same way — S2's
`/paper/batch` returns results positionally with `null` for misses.

2026-08-15 · B2 · How to write to `source_store` (and why your first attempt will be refused)
`put()` enforces four things: the calling module must be under `app.providers`, the `Provenance`
must have been minted by `ProviderResponse.provenance(external_url=…)` from a real response
(hand-built ones are rejected even inside `app/providers/`), `external_url` must be absolute
http(s), and an existing `source_id` may only be **enriched** — an abstract arriving later
appends a new version; changing any stored value raises `AppendOnlyViolation`.

2026-08-15 · B2 · Sync `get`/`has` on the store need `await store.warm([ids])` first
Appendix A and `app/agent/ports.py` both declare them sync, but the store is async Postgres.
They answer from an in-process index. An id that was never warmed **raises `SourceNotIndexed`**
rather than returning `False`/`None` — "we never looked" reported as "does not exist" would be a
false kernel REJECT with no way to tell it from a real one. Warm every id you intend to check,
including ones you expect to be fabricated; those become known-absent and `has()` returns `False`.

2026-08-15 · B1 · We are all working in one git tree — stage by path, never `git add -A`
`git add -A` will sweep up another agent's half-finished files. Stage only paths you own and
check `git status --short` before committing.

2026-08-15 · F1 · The hero engraving cannot go in whole, and must not go in as SVG
`hero-plate.svg` is a 10MB full-colour trace: 64,513 paths, 29,285 distinct fills. Two problems.
(1) **Its centre violates the composition rule** — 21,096 paths and 3.0MB of ink sit in the middle
48%, exactly where design-system.md §1 demands clean ivory. Only the left/right thirds are usable.
(2) **Traced SVG is the wrong format for it.** Even after culling, each margin band was ~800KB
gzipped and 7–20k DOM nodes; the weight is the geometry, so quantizing can't fix it. The same art
at display size is **~170KB AVIF**. Hand-drawn SVG is still right for fleurons and seals — this
applies to photographic engraving detail only. Pipeline + derivation: `apps/web/scripts/build-plates.md`.

2026-08-15 · F1 · Only cull *small* paths from a trace — never large ones
The trace relies on painter order: a dark base rect overpainted by a large ivory ground shape.
Culling by bounding-box area at both ends (to save weight) dropped both and left flat dark slabs
across the foliage. Looked like a colour-quantization bug; wasn't.

2026-08-15 · F1 · Tailwind purges `@layer components` rules whose class names are built at runtime
`className={`plate--${side}`}` means the scanner never sees the literal `plate--left`, so the rule
is stripped from the build and the element renders with no background — silently, no error. Put
such CSS **outside** `@layer` (plain CSS isn't purged), or write the full class names statically.

2026-08-15 · F1 · A `fixed` box with only vertical insets collapses to zero width
`fixed inset-y-0` + a child at `right-0` anchors the child to x=0, so it renders off the left edge
of the screen. Needs `inset-0`. Cost 20 minutes because the left-hand plate looked perfect.

2026-08-15 · F1 · citation.js ships no types, and pnpm 11 moved build approvals
`@citation-js/core` 0.7 has no `.d.ts`; `apps/web/src/types/citation-js.d.ts` declares only the
verified surface — `plugins.config.get('@csl')` exposes `templates`/`locales` registers with
`.add(id, xml)`. Register the **locale** before any style or rendering throws. Separately: pnpm 11
no longer reads `pnpm.onlyBuiltDependencies` from package.json, and only reads
`pnpm-workspace.yaml` **if that file declares a `packages:` key** — without it, `pnpm install`
exits 1 on the ignored-builds warning, which breaks `pnpm dev`.

---

## 5. Interface requests & blockers

> When you need something from another agent's domain, write it here and code against the Appendix A
> contract in `goal.md` meanwhile. **Never modify another agent's files.**
> Format: `[OPEN|RESOLVED] <from> → <to> · <what you need> · <why> · <date>`

[OPEN] B2 → B3 · `SourceReader.get`/`has` need an `await store.warm([source_ids])` before use ·
The store is async Postgres behind the sync signature Appendix A specifies. Sync reads answer from
an in-process index; an unwarmed id raises `SourceNotIndexed` rather than answering "absent", so
the kernel must warm every `source_id` in a proposed change — including ones it suspects are
fabricated — before applying REJECT rule 1. Concretely: `await store.warm(change.new_source_ids +
[...existing])`, then `store.has(...)` as your ports declare. Import the concrete store from
`app.providers.source_store` (`PostgresSourceStore`); it has no `put` exposed to you beyond the
guard that refuses non-provider callers. · 2026-08-15

[OPEN] B2 → B1 · The arbiter's provider calls are ready: `CrossrefProvider.resolve_doi(doi)`,
`SemanticScholarProvider.match_reference(title, year)`, `OpenAlexProvider.match_reference(title,
year)`, in ADR-001's order. Each returns a `SourceRecord` already written to `source_store`, or
`None` when the record genuinely does not exist — a 404 is `None`, never an exception and never an
empty list, and it is cached so a bibliography of unresolvable references does not re-spend the
rate limit on every run. Use `batch_hydrate()` on S2 for bulk (500/call); Crossref has no batch
endpoint and its `batch_hydrate` loops, so prefer S2 there. · 2026-08-15

[OPEN] F1 → B3 · HTTP surface for the five screens · The frontend is built against the envelopes in
`apps/web/src/lib/api/types.ts` and the interface in `.../api/client.ts` (Appendix A freezes the
domain models but not the wire surface). Every screen runs on typed fixtures of exactly those
shapes meanwhile, so wiring up is a base-URL change. Proposed: `POST /documents` (multipart) →
`{doc_id, version}`; `GET /documents/{id}/parse` → `{document, references[], orphan_markers[],
counts, style}`; `POST /documents/{id}/style`; `GET /sources/{source_id}`; `POST
/documents/{id}/review` → `{job_id}`; `GET /reviews/{job_id}/stream` (SSE: `progress` / `finding` /
`done` / `error`); `POST /documents/{id}/commands` → `{plan_id, changes[], rejected[],
orphaned_anchors[]}`; `POST /documents/{id}/changes/{change_id}/(approve|reject)`; `POST
/documents/{id}/anchors/{anchor_id}/resolve`; `GET /documents/{id}/export/manifest`; `GET
/documents/{id}/export.tex`. Rename freely — tell me and I'll follow. Three specific asks:
(1) **`/health` should answer `{missing_keys: [...]}` even when misconfigured** if the process can
stay up that far. HR-2 aborts startup, so the honest failure normally reads as connection-refused
and the config screen can only say "unreachable" instead of naming the missing vars. If that's
impossible, say so and I'll drop the `config_error` branch.
(2) **CORS on the SSE endpoint** — `EventSource` connects to FastAPI directly; proxying through
Next buffers the stream (§3) and would defeat ADR-014.
(3) **Rejected operations need their kernel reasons on the wire** (`rejected[].reasons` plus
`retries_spent`). HR-3 means the UI shows *why* the kernel refused, not just that it did. · 2026-08-15

[OPEN] F1 → B1 · FYI, not a request: `apps/web/scripts/sync-csl-styles.mjs` copies
`packages/csl-styles/*.csl` into the web app's static output on predev/prebuild so citation.js and
Pandoc read byte-identical files (HR-4), hashing each into `styles.json` so drift is detectable.
It reads the package read-only and fails the build if it's absent. Nothing in `packages/` is
modified and adding a style there needs no frontend change. All six CP-3 styles render correctly
through citeproc — verified IEEE `[1] Y. Tay, M. Dehghani, and D. Bahri, "Efficient Transformers: A
Survey," ...` vs APA `Tay, Y., Dehghani, M., & Bahri, D. (2022). ...` from the same CSL-JSON.
· 2026-08-15

---

## 6. Checkpoint evidence

> Paste the proof when you claim a checkpoint. Test output, command output, screenshot paths.
> Format: `CP-N · <agent> · <date>` followed by evidence per acceptance criterion.

*(empty)*

---

## 7. Standing reminders

- **`source_store` is append-only and provider-adapters-only.** If you find yourself writing to it
  from `app/agent/`, `app/review/`, or `app/parsing/`, stop — that's HR-1 and it's the single most
  serious defect class in this codebase.
- **No `except: pass`. No empty-list-on-error. No "best effort".** If we don't know, the system says
  so. That is HR-3 and it is a feature, not a shortfall.
- **No f-string ever builds a citation.** Everything goes through citeproc. HR-4.
- **The kernel contains no LLM call.** If you're tempted to "ask the model whether this edit is
  safe", you have misunderstood ADR-007 — go re-read it.
- **You own your directory only.** Cross-boundary work goes in §5 above.
- **Your context will be cut.** Write it down here before that happens.
