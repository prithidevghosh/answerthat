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

2026-08-15 · B3 · Build the kernel first — it needs no model and no other agent's code
The invariant kernel is pure code over Appendix A types. It was written and passing 31
adversarial tests before a planner, an executor or a single collaborator existed. If you feel
blocked waiting on another agent, check whether the thing you're waiting for is actually in the
dependency path. For the kernel it isn't, and that is ADR-007 paying out.

2026-08-15 · B3 · `Operation.params` is an untyped `dict` — type it at your boundary
Appendix A leaves `params` open. That is the one field where a planner could smuggle free text
into the pipeline. `app/agent/operations.py` has a params model per operation and
`parse_params()` refuses anything that doesn't validate. Call it before you trust `params`.

2026-08-15 · B3 · "Newly asserted claim" needs a mechanical definition or REJECT rule 3 is unusable
You cannot ask a model "is this a new claim?" — that is the probabilistic check ADR-007 rejects.
The kernel's rule: a span whose id is new **or whose text changed** is a new assertion *unless*
the executor declared it derived from spans that really existed in the before-document
(`ChangeContext.derived_spans`; the kernel verifies those ids existed, so a fabricated
derivation is itself a reject). Rewritten text is derived, invented text is not. The transform
pipeline fills this in for free, so typed transforms never trip it and an invented paragraph
always does.

2026-08-15 · B3 · A surfaced orphan anchor would trip the HR-5 multiset check unless you hold it
Raising an anchor for a user decision removes it from the document, which looks exactly like
citation loss. The kernel counts `reachable(after)` **plus** the sources of anchors listed in
`ProposedChange.orphaned_anchor_ids` — held, not lost. That is why FLAG-2 and REJECT-2 compose
instead of fighting: surface the anchor and it's a flag, drop it and it's a reject. The same
edit yields either verdict depending only on whether you surfaced it.

2026-08-15 · B3 · Keep `best_span_id` when an anchor lands below threshold
"Keep it here" is only an option the UI can offer if you remembered where the anchor nearly
landed. `ReattachmentRecord.best_span_id` is the argmax regardless of threshold, and it is what
makes ADR-013 step 4 a three-way choice rather than a two-way one.

2026-08-15 · B3 · Re-run the kernel at commit time, not only at proposal time
A user approving a *subset* of a change set produces a document the kernel never judged.
`VersionService.commit` re-evaluates each approved change against the evolving document, then
checks the composed base→final multiset once more before writing. Per-change verdicts are not a
substitute for the end-to-end statement, and the composed check has its own test.

2026-08-15 · B3 · Assert "no model call" against the AST, not the file text
A substring scan for `await`/`anthropic` over `kernel.py` fails the moment a docstring mentions
either word, and passes if a violation hides behind an alias. `ast.walk` for `Await`/
`AsyncFunctionDef` nodes plus the module's actual import list says what you mean.

2026-08-15 · B3 · pytest-asyncio is strict here; a module-level `pytestmark` warns on sync tests
`pytestmark = pytest.mark.asyncio` applies to sync tests in the same file and each one warns.
Put `@pytest.mark.asyncio` on the async tests individually.

2026-08-15 · B3 · Hold a *session factory*, not an `AsyncSession` — B1's stores take a session
`PostgresDocumentStore(session)` and `PostgresSourceStore` are per-unit-of-work. Wiring one into
a process-lifetime singleton at startup accumulates a transaction across unrelated requests, so
one bad edit poisons the next request's reads. `app/api/adapters.py` holds the *class* plus
`app.core.db.session_scope` and opens a session per call. mypy caught this — it presented as
`Argument 1 has incompatible type "Any | None"`, which is worth not waving through.

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

[RESOLVED] B3 → F1 · **The HTTP surface is live.** Answering your request above. All routes are
under an `/api` prefix — otherwise I followed your naming wherever I could. Generated from the
running app, so this is the surface, not a proposal:

```
POST   /api/documents                          multipart `file` → 202 {job_id, doc_id, poll}
GET    /api/documents/{id}/parse-status        {state, stage, progress, version, error, ...}
GET    /api/documents/{id}/parse               {document, references[], orphan_markers[],
                                                counts, quarantine[], style}   ← your parse screen
GET    /api/documents/{id}                     Document (Appendix A); ?version= for any version
GET    /api/documents/{id}/versions            {doc_id, versions[], current}
POST   /api/documents/{id}/revert              {to_version} → CommitResult
GET    /api/documents/{id}/style               {style_id, score, ambiguous, shortlist[]}
PUT    /api/documents/{id}/style               {style_id}
POST   /api/documents/{id}/review              → 202 {job_id, poll, stream}
GET    /api/documents/{id}/review/status       {state, verified, total}
GET    /api/documents/{id}/review/stream       SSE — connect EventSource here DIRECTLY
POST   /api/documents/{id}/commands            {command, version?} → ProposedChangeSet
GET    /api/change-sets/{cs_id}                ProposedChangeSet
GET    /api/documents/{id}/change-sets         ProposedChangeSet[]
POST   /api/change-sets/{cs_id}/approve        {approved_change_ids[], rejected_change_ids[],
                                                orphan_decisions[]} → CommitResult
GET    /api/documents/{id}/export.tex          text/x-tex, Content-Disposition attachment
GET    /api/agent/metrics                      the ADR-009 FreeformEdit tripwire
GET    /api/health                             {status, bound{}, unbound[]}
```

Your three asks:

(1) **`/health` cannot report `missing_keys`, and that is HR-2 working.** If a required key is
absent the process does not come up — `MissingAPIKeyError` propagates out of the app factory by
design (ADR-010), so there is no server left to answer. Connection-refused *is* the honest
signal; keep your "unreachable" branch. What I can give you: the container writes
`CONFIGURATION ERROR: <which key and why>` to stderr before dying, so `docker compose logs api`
names it. And `/api/health` does report the *other* half of your config screen — `unbound[]`
lists collaborators that aren't wired, and any route needing one returns **503**
`{error: "dependency_unavailable", component, detail}` rather than a 500 or an empty result.

(2) **CORS is on, including the SSE endpoint.** Origins default to `localhost:3000` /
`127.0.0.1:3000`, overridable via `settings.cors_origins`. The stream also sets
`X-Accel-Buffering: no` and `Cache-Control: no-cache`. Connect `EventSource` straight to
FastAPI — do not proxy through a Next route handler (§3).

(3) **Kernel reasons are on the wire.** `ProposedChangeSet.rejected[]` is
`{operation, reasons[], attempt}` where `reasons[]` are the kernel's strings verbatim, each
prefixed with a stable machine code (`unknown_source_id`, `citation_multiset_shrank`,
`ungrounded_new_claim`, `ir_schema_violation`, `pandoc_refused`). Your `retries_spent` is called
**`attempts`** (1-based, max 3 = initial + 2 retries per ADR-007). `status` is
`awaiting_approval` or `failed`; `failed` means nothing survived and there is nothing to approve.

Four differences from your proposal worth reading:

* **Approval is one batched call, not per-change.** Changes within a set are *sequenced* — each is
  validated against the document as the previous one left it — so approving them individually
  would invite committing an incoherent subset. Collect per-change approve/reject in your UI
  exactly as CP-7 requires, then submit the decisions together. Anything you approve that no
  longer applies comes back in `CommitResult.skipped{change_id: reason}` rather than being
  dropped quietly.
* **`/anchors/{id}/resolve` doesn't exist; orphan decisions ride on the approve call.** Each
  change carries `orphans[]` = `{anchor_id, marker, source_ids[], best_span_id, best_span_text,
  score, threshold, actions:["keep","move","remove"]}`. `best_span_text` is there so you can
  render "keep it here" against real text. **An undecided orphan blocks the commit with a 409** —
  it defaults to neither keep nor remove (ADR-013 step 4).
* **Upload returns a job, not a document.** Ingest is a background job; poll `/parse-status`.
* **Every change carries a structural diff.** `changes[].diff.citations` is a `CitationLedger`:
  `{preserved, total_before, total_after, sources_lost{}, sources_gained{}, anchors[]}` where each
  anchor is `{anchor_id, status, before_span_id, after_span_id, source_ids_before/after, note}` and
  `status` ∈ `unchanged|moved|source_changed|added|held_for_decision|removed`. That list *is* the
  HR-5 evidence — showing it is how the user comes to believe the guarantee. `preserved` plus
  `headline` give you a one-line summary if you want one.

Full request/response schemas are in the live OpenAPI at `/openapi.json` and `/docs`. · 2026-08-15

[OPEN] B3 → B1 · Two small things the API surface needs from ingest/style ·
(1) `app.parsing.pipeline` needs a module-level `get_ingest_pipeline(settings)` returning an
object with `enqueue(doc_id, filename, payload) -> job_id`, `status(doc_id) -> dict | None`
(keys: `state` ∈ queued|running|complete|failed, `stage`, `progress`, `version`, `error`),
`parse_report(doc_id) -> dict` (keys: `references`, `orphan_markers`, `counts`) and
`record_failure(doc_id, message)`. `parse_report` backs F1's parse-inspector screen — the tier
counts and orphan markers are yours, I only compose them with the IR.
(2) `app.parsing.style` needs `get_style_service(settings)` with `detect(doc_id)` and
`select(doc_id, style_id)` returning `{style_id, score, ambiguous, shortlist}`.
Until these land, `/api/documents/{id}` and the edit flow work fully; upload, `/parse` and
`/style` return **503 naming the missing component**, never a stub. I've deliberately not
guessed at your internals — if you'd rather expose different names, tell me and I'll adapt
`app/api/deps.py`. · 2026-08-15

[OPEN] B3 → B1 · `app/main.py` doesn't exist and `memory.md` §2 tells everyone to run
`uvicorn app.main:app` · My factory lives at `app.api.main:create_app()` with
`app.api.main:asgi` as a uvicorn factory entry point, i.e. `uvicorn --factory app.api.main:asgi`.
Either update §2 / the Dockerfile to that, or add a two-line `app/main.py` doing
`from app.api.main import asgi; app = asgi()` — your call, it's outside my paths. **Do not**
wrap it in a try/except: `build_services()` must be allowed to raise `MissingAPIKeyError` and
stop the process (HR-2). · 2026-08-15

[OPEN] B3 → B2 · Four factories for the review services the edit path calls ·
`app.review.retrieval:get_retrieval_service(settings)` → `find_candidates(claim, limit) ->
list[source_id]`; `app.review.verify:get_verification_service(settings)` → `verify(claim_text,
source_id) -> Verification`; `app.review.claims:get_claim_extractor(settings)` →
`extract(document, target_ids) -> list[Claim]`; `app.review.runner:get_review_runner(settings)` →
`start(doc_id, section_ids) -> job_id`, `status(doc_id) -> dict`, `stream(doc_id)` as an async
iterator of `(event_name, payload)` tuples, `run(...)`, `record_failure(doc_id, message)`.
`AddCitations`/`FindSupport` need the first three; the SSE endpoint needs the fourth. Event names
I emit downstream: `finding`, `progress`, `error`, `complete`, `heartbeat` — yield whichever of
the first four apply and I'll frame them. Names are negotiable; the shapes are what I've built
against. · 2026-08-15

[RESOLVED] B3 → B2 · Your warming contract is implemented as you specified · `warm()` is called
on all three paths that reach a source lookup — the command loop, the commit path, and
`ReplaceCitation` — with exactly the ids REJECT rule 1 will check, computed by the pure helper
`InvariantKernel.referenced_source_ids(change)`. Fabricated ids are warmed too, so they come back
known-absent and the reject rests on a real answer rather than on a `SourceNotIndexed`. Warming
deliberately does **not** happen inside the kernel: it stays pure and synchronous, with no reason
to await anything (ADR-007). Three tests cover it, one using a strict fake that reproduces your
raise-on-unwarmed behaviour. · 2026-08-15

---

## 6. Checkpoint evidence

> Paste the proof when you claim a checkpoint. Test output, command output, screenshot paths.
> Format: `CP-N · <agent> · <date>` followed by evidence per acceptance criterion.

### CP-4 — Providers · B2 · 2026-08-15 · **complete except live-Postgres verification**

```
$ cd services/api && uv run pytest tests/unit/b2 -q
163 passed in 0.66s

  12  test_key_enforcement.py        8  test_ratelimit.py         7  test_cache_keys.py
  22  test_semantic_scholar.py      28  test_openalex_and_crossref.py
  17  test_source_store_hr1.py      21  test_provider_protocol.py  8  test_postgres_schema.py
  40  test_review_pipeline.py

$ uv run ruff check app/providers app/review tests/unit/b2
All checks passed!
```

| Criterion | Evidence |
|---|---|
| Adapters for S2 / OpenAlex / Crossref behind the `Provider` protocol | `test_provider_protocol.py` — all four Appendix A methods present, async, and with matching parameter names on all three adapters (`Provider` is not `@runtime_checkable`, so this is the only conformance check that exists) |
| Both keys required, `MissingAPIKeyError`, no fallback (HR-2) | `test_key_enforcement.py` (12) + `test_construction_raises_without_a_key` on each adapter. `test_there_is_no_anonymous_constructor_argument` also pins the constructor *shape* — ADR-010's failure mode arrives as `allow_anonymous=True` long before it arrives as a deleted check |
| Token-bucket limiter per provider (S2 ~1 rps; OpenAlex credit-aware) | `test_ratelimit.py` — pacing, FIFO fairness, raise-past-budget, `penalise()` after a 429; credits charged per endpoint class (1/10/100/1000) with a refused charge not recorded |
| All calls send `mailto` for the polite pool | `test_every_openalex_call_carries_key_and_mailto`, `test_crossref_calls_carry_mailto` — asserted on the **request**, in both the query string and the User-Agent |
| Response cache in Postgres keyed by `(provider, endpoint, normalized_query_hash)` with TTL | `test_cache_keys.py` (key derivation, normalization, expiry) + `test_postgres_schema.py` (DDL compiles against the real PostgreSQL dialect; `ix_provider_cache_lookup` is exactly those three columns; upsert compiles to `ON CONFLICT DO UPDATE`). **Not yet run against a live server — see the caveat below.** |
| `abstract_inverted_index` inverted to plain text | `test_invert_abstract_*` — reconstruction, sparse positions, and `None` (not `""`) for an empty index |
| Abstract fallback chain S2 → OpenAlex inverted → S2 TLDR → `unavailable` | Five tests in `test_openalex_and_crossref.py`, including that a full OpenAlex abstract outranks an S2 TLDR, and that a `ProviderRateLimited` mid-chain **propagates** rather than becoming "no abstract" |
| Provider adapters are the only writers to `source_store` — enforced, not documented | `test_source_store_hr1.py` (17). Four runtime guards, each with an attack test: caller module must be under `app.providers`, provenance must have been minted by `ProviderHTTP` from a real response, `external_url` must be absolute http(s), and an existing record may only be *enriched*. Plus `test_no_review_module_writes_to_the_source_store` and `test_the_store_contains_no_update_or_delete_at_all` |

**Caveat, stated rather than discovered:** Docker is unavailable in this environment, so
`PostgresResponseCache` and `PostgresSourceStore` have **not** been exercised against a running
Postgres. What is proven offline: both tables are registered on B1's `Base` (so `create_all()`
creates them), the DDL and every statement compile against the real PostgreSQL dialect, and the
store's module emits no `UPDATE`/`DELETE`/`ON CONFLICT` at all. What is **not** proven: that a
round trip through asyncpg works. The `InMemory*` variants carry the identical guards and logic,
so the behaviour under test is production code — but T1 should run these two classes against the
compose Postgres before the honesty audit signs off. Nothing else in CP-4 depends on it.

### CP-5 — Review · B2 · 2026-08-15 · **complete on my side; the SSE endpoint is B3's**

| Criterion | Evidence |
|---|---|
| Atomic claims carrying `span_id` + `anchor_ids` + `citability` | `test_review_pipeline.py` claims section. The model returns **character offsets**, claim text is sliced from the paper in code, and a claim whose echoed quote disagrees with its offsets is dropped (`test_a_claim_whose_quote_disagrees_with_its_offsets_is_dropped`). `anchor_ids` are computed from offsets — `test_anchor_ids_are_computed_from_offsets_not_supplied_by_the_model` |
| All three candidate strategies live | `test_all_three_strategies_run_for_a_claim` asserts each of snippet search, Recommendations-from-bibliography, and OpenAlex search + one-hop expansion is actually invoked, **with the claim text as the query, not the section** |
| RRF, dedupe by DOI/S2 id, subtract everything already cited | `test_reciprocal_rank_fusion_rewards_agreement_across_strategies`, `test_the_same_paper_from_two_providers_collapses_to_one_candidate`, `test_a_doi_less_paper_collapses_on_normalized_title_and_year`, `test_everything_already_cited_is_subtracted` |
| Rerank scores against **the claim**, not the topic | `test_rerank_sorts_by_claim_relevance_not_by_fused_rank` — the famous survey wins on fused rank and loses on claim relevance. `test_rerank_discards_a_source_id_it_was_not_given` keeps HR-1 intact through the rerank step |
| Verifier returns one of the five labels | Four via the schema `enum`; `unverifiable_no_abstract` is produced by code before any model call (`test_no_abstract_yields_the_fourth_outcome_without_calling_the_model`) |
| Verbatim quote + mechanical substring check kills the finding | Seven tests on `quote_is_present` alone: verbatim passes, **paraphrase dies**, fluent invention dies, re-encoded punctuation and reflowed whitespace still pass, a sub-25-char fragment is rejected, and stem/stopword matching is asserted **absent**. End to end: `test_verifier_kills_a_finding_whose_quote_is_not_in_the_abstract` |
| The same verifier serves both callers | `verify_detailed()` is called for candidates and for existing anchors in `stream.py`. `test_an_existing_anchor_that_does_not_support_its_claim_is_a_finding` and `test_a_supported_existing_anchor_produces_no_finding` |
| Findings ordered by citability descending with `verified / total` | `test_findings_arrive_in_descending_citability_order`, `test_progress_reports_verified_over_total_throughout` (`claims_verified` goes 0→1→2→3 against `claims_total`) |

**Honestly scoped:** the last criterion says "stream over **SSE**". I provide the ordered async
generator (`ReviewRunner.stream(doc_id, section_ids=...)` yielding `(event_name, payload)`, per
B3's `ReviewRunner` port); the SSE endpoint that serves it is `app/api/`, which is B3's. CP-5 is
complete on my side of that line and is joint at the boundary.

Also verified beyond the checklist, because a short feed must be explicable rather than
ambiguous: zero candidates after subtraction emits a `no_candidates_found` finding rather than
silence; `unverifiable_no_abstract` is emitted and displayed; `does_not_address` is counted in
progress rather than emitted; and every quote-check kill increments `quote_check_failures` in the
progress payload.

### CP-6 — Agent core · B3 · 2026-08-15

```
$ cd services/api && .venv/bin/python -m pytest tests/unit/b3 -q
109 passed in 0.45s

  test_kernel_adversarial.py   31    test_loop_and_versioning.py  21
  test_transform.py            14    test_api.py                  21
  test_executor.py             22
```

Run against B1's real `app/core/contracts.py` (confirmed loaded from
`services/api/app/core/contracts.py`, not the Appendix A bootstrap — that shim is now inert).

**Scope of the evidence, stated up front.** These are unit tests over B3's code with B1/B2
collaborators replaced by fakes that reproduce their published contracts — including B2's
raise-on-unwarmed source store. They do **not** demonstrate the pipeline end to end against a
real PDF, GROBID, or live providers; that is T1's CP-8 and it is not claimed here.

| CP-6 criterion | Evidence |
|---|---|
| Planner emits `EditPlan` as structured output only — cannot emit prose or raw text edits | `app/agent/planner.py:edit_plan_schema()` — the schema has no free-text or `source_id` field; `AnthropicStructuredModel` uses a forced `tool_choice` and **raises** rather than parsing a plan out of free text. `Planner._materialize` rejects any payload without `operations`. |
| All seven operations implemented | `test_executor.py` exercises each: `AddCitations`, `FindSupport`, `Shorten`, `RewriteSection`, `ReplaceCitation`, `MoveText`, `FreeformEdit` — each asserted through to a kernel verdict, not just to a return value. |
| `FreeformEdit` requires `no_typed_op_applies` + justification, firing rate logged | `test_freeform_edit_without_the_gate_is_refused`, `..._without_a_justification_is_refused`, `test_the_gate_flag_is_meaningless_on_a_typed_operation`. Rate at `GET /api/agent/metrics`; `test_the_tripwire_trips_above_twenty_percent` pins ADR-009's ~20%. |
| Invariant kernel is pure code with no LLM call; REJECT and FLAG correctly separated | `test_kernel_module_makes_no_model_call` checks the **AST**: no `Await`/`AsyncFunctionDef` nodes, no model-library imports, and the only `ports` imports are `RenderProbe` and `SourceReader`. All 5 REJECT rules and all 3 FLAG rules have dedicated tests; `test_reject_beats_flag` pins precedence; `test_every_reject_carries_at_least_one_reason` pins HR-3. |
| Detach → transform → reattach; the text model never receives citation markers | `test_the_text_model_never_sees_a_citation_marker` inspects what the model was actually handed. `MarkerLeakError` is raised if a marker reaches the boundary; marker-shaped model *output* is stripped and reported (`test_marker_shaped_output_from_the_model_is_stripped_and_reported`). |
| Anchors below threshold produce a user-facing decision, never a deletion | `test_an_anchor_with_no_home_is_surfaced_not_dropped`, `test_a_model_returning_nothing_orphans_every_anchor_rather_than_losing_it`, `test_an_undecided_orphan_blocks_approval_with_a_409`. `test_surfacing_the_same_anchor_turns_the_reject_into_a_flag` shows the two verdicts differ *only* by whether the anchor was surfaced. |
| REJECT returns the reason to the planner, max 2 retries, then surfaces | `test_a_rejection_is_handed_back_to_the_planner_with_its_reason` asserts the kernel's text appears in the next prompt; `test_retries_stop_at_two_and_the_reason_survives` asserts exactly 3 model calls and the reason intact on the wire. |
| Every approved change set commits a new IR version; every version is revertible | `test_approving_a_change_commits_a_new_revertible_version`, `test_versions_and_revert`. `test_approving_nothing_writes_no_version` confirms the gate. |

**The three adversarial attacks (also CP-8's kernel suite), each asserted REJECT:**

```
fabricated source_id  test_rejects_fabricated_source_id_in_an_anchor
                      test_rejects_fabricated_source_id_declared_only_in_new_source_ids
                      test_an_executor_cannot_introduce_a_source_the_store_does_not_have
dropped anchor        test_rejects_a_shortened_paragraph_that_quietly_loses_a_citation
                      test_rejects_deleting_a_block_that_carries_anchors
                      test_rejects_an_anchor_that_found_no_home_and_was_not_surfaced
unsupported new claim test_rejects_a_new_paragraph_asserted_with_no_verified_anchor
                      test_rejects_a_new_claim_whose_anchor_does_not_support_it
                      test_rejects_a_fabricated_derivation
```

**Two known limits, stated rather than discovered later:**

1. **`FreeformEdit` grounding is weaker than the typed ops', by design.** A freeform rewrite
   declares its output derived from the spans it rewrote, so REJECT rule 3 does not fire on new
   propositional content smuggled into a rewrite of existing text. Citation safety is unaffected —
   HR-1 and HR-5 hold identically — but "this sentence asserts something new" is only caught for
   text with no derivation at all. This is ADR-009's stated trade ("only the generic invariants
   apply"), and the tripwire metric exists so we can see if we are leaning on it.
2. **`ChangeSetStore` is process-local.** Propose and approve both happen in the API process, so
   this is correct today; it is not correct the moment approvals can arrive at a different worker.
   Noted in `app/agent/store.py` with what to do about it.

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
