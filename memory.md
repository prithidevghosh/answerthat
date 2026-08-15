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
