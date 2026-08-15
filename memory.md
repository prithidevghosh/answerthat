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
| `OPENAI_API_KEY` | **all** LLM and embedding calls (ADR-015, ADR-016) |
| `GROBID_URL` | defaults to `http://grobid:8070` in compose |
| `LLM_MODE` | `live` \| `record` \| `replay`. CI runs `replay` (ADR-018) |

**Models are chosen per role, not globally (ADR-015).** Every model ID is pinned in
`app/core/config.py` and appears **nowhere else**:

| Role | Model |
|---|---|
| reference repair | `gpt-5.4-mini` |
| claim extraction | `gpt-5.4` |
| candidate rerank | `gpt-5.4-mini` (after the embedding prefilter) |
| **verification** | **`gpt-5.5`** — accuracy-critical, do not economise |
| planner | `gpt-5.5` |
| text transform | `gpt-5.4` |
| embeddings | `text-embedding-3-small`, `dimensions=512` |

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

**Never call OpenAI directly.** Every LLM and embedding call goes through `app/core/llm.py`. Calling
the SDK from your own module bypasses per-role routing, structured output, the token budget, and
record/replay — and it will break CI, which runs with zero live calls. *(all backend)*

**Structured output is mandatory, not optional.** Pass a JSON Schema for every data-returning call.
Prompt-and-parse is prohibited — it is how the planner ends up emitting prose it was designed not to
be able to emit (ADR-007). *(all backend)*

**LLM output is not reproducible.** Don't try to fix that with temperature or seeds. Record once,
replay forever; a replay cache miss raises. *(all backend, T1)*

**Prompts live in files inside your own package** — `app/parsing/prompts/`, `app/review/prompts/`,
`app/agent/prompts/`. Not inline strings, not a shared directory (ADR-019). *(all backend)*

**Table name prefixes are per-agent**, and there are no migrations in v1: B1 `ir_*`, B2 `src_*`,
B3 `agent_*`; `create_all()` at startup, `make db-reset` to wipe (ADR-020). Do not create a table
outside your prefix. *(all backend)*

**Fingerprint vectors are never stored inline in the IR** — `fingerprint_id` points at the
`anchor_fingerprints` table. Inline vectors duplicate per version and make structural diffs
unreadable, and that diff is how the user verifies HR-5 with their own eyes (ADR-017). *(B1, B3)*

---

## 4. Learnings log

> Append new entries at the bottom. Format: `YYYY-MM-DD · <agent> · <one-line title>` then 1–4 lines.
> Include the thing you'd have wanted to know an hour earlier.

*(empty — first agent to learn something non-obvious, start here)*

---

## 5. Interface requests & blockers

> When you need something from another agent's domain, write it here and code against the Appendix A
> contract in `goal.md` meanwhile. **Never modify another agent's files.**
> Format: `[OPEN|RESOLVED] <from> → <to> · <what you need> · <why> · <date>`

*(empty)*

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
