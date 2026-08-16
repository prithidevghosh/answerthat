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

2026-08-16 · B2 · The review pipeline was on the Anthropic SDK; migrated to `app/core/llm.py`
`app/review/llm.py` built its own `AsyncAnthropic` client with `DEFAULT_MODEL =
"claude-opus-5"` hardcoded. That bypassed per-role routing, the token budget and
record/replay all at once — and would have made CI issue live calls. It is now a thin
`ReviewLLM` adapter over B1's client. If you are adding a stage, declare an `LLMRole`; do
not name a model. `tests/unit/b2/test_review_pipeline.py` greps `app/review/` for model
IDs and SDK imports so this cannot come back quietly.

2026-08-16 · B2 · `composition.get_llm()` read `settings.anthropic_api_key`, which no longer exists
Nothing caught it because the only test that constructed the bundle failed earlier, on
`Settings()` refusing to build without `OPENAI_API_KEY`. Lesson: a fixture that skips the
credential check hides every downstream break behind one error. `tests/unit/b2/conftest.py`
now sets all three fake keys autouse plus `LLM_MODE=replay`, so config is exercised for
real and a stray model call fails on a missing recording rather than billing anyone.

2026-08-16 · B2 · The embedding prefilter was missing, and its absence is silent
ADR-015's cost control is the cascade, not a cheaper verifier. Fusion went straight to the
`RERANK` model with no prefilter, and `keep_top` was a literal `5` rather than
`VERIFY_KEEP`. Nothing fails when the prefilter is absent — the review is still correct,
just several times more expensive per claim, which is exactly the pressure that later gets
answered by downgrading `VERIFY`. `app/review/prefilter.py` now runs first and
`build_review_runner()` wires it, so no caller has to remember.

2026-08-16 · B2 · `openai` was not in `pyproject.toml` at all
`app/core/llm.py` imports it lazily, so `LLM_MODE=replay` and the whole unit suite pass
without it — live and record mode would have failed at the first call. Added, and the two
dead SDKs (`anthropic`, `voyageai`) removed: a second model SDK in the lockfile is a second
one somebody can import.

2026-08-16 · B1 · A Protocol with no implementation is a pipeline stage that never runs
`repair.py` had `ReferenceSegmenter` as a `Protocol`, every adversarial test passing against a
fake, and no production implementation anywhere. `get_ingest_pipeline` defaulted
`segmenter=None`, so ADR-003 was dead code on every real upload while looking fully built and
fully tested. If a stage is optional in its constructor, grep for who supplies it before
believing it runs.

2026-08-16 · B1 · Default arguments are a second copy of a threshold
`Arbiter(accept_threshold=0.85)` and `detect_style(ambiguity_margin=0.05)` carried the ADR-024
values as defaults. Every caller passed `settings.…` correctly, so nothing was visibly wrong —
until T1 sweeps the config and one forgotten keyword silently keeps the old number. Both are now
required keyword arguments: the call fails rather than quietly disagreeing with config.

2026-08-16 · B1 · OpenAI strict structured output has no optional fields
`strict: true` requires `additionalProperties: false` and *every* property listed in `required`,
at every level of nesting. "Optional" is expressed as `"type": ["string", "null"]`. For the
repair tier this is a happy accident: the model must actively answer `null` rather than omit a
key, and a schema that *required* a DOI would be a schema instructing the model to invent one.

2026-08-16 · B1 · The substring check runs on our CSL, not on the model's answer
`csl_from_segmentation()` reshapes the flat schema response into CSL-JSON *before*
`check_substring_containment` sees it. A mapping bug that manufactures a value — gluing a particle
onto a family name, carrying an empty string through — walks straight past the mechanism designed
to catch fabrication, because `""` is a substring of everything. `test_segmenter.py` tests the
mapping for exactly that reason.

2026-08-16 · B1 · `.env.example` and `docker-compose.yml` are part of HR-2, not documentation
`config.py` made `OPENAI_API_KEY` startup-fatal while compose was still passing
`ANTHROPIC_API_KEY`. The code was correct and the deployment was guaranteed to abort — CP-1's
first criterion, failing for a reason no unit test can see. Adding a required key changes three
files, not one.

---

## 5. Interface requests & blockers

> When you need something from another agent's domain, write it here and code against the Appendix A
> contract in `goal.md` meanwhile. **Never modify another agent's files.**
> Format: `[OPEN|RESOLVED] <from> → <to> · <what you need> · <why> · <date>`

[RESOLVED] B2 → B3 · IR-5, the review runner factory · `app/api/deps.py:144` reports
"B2's streaming review runner is not wired". `app/review/` must not import B1's
`DocumentStore` and B3's composition root owns it, so neither side could build the runner
alone. B2's half now exists: `app.review.composition.build_review_runner(document_store)`
returns a fully wired `ReviewRunner` — extractor, candidates, prefilter, reranker,
verifier. `get_review_runner()` in `app/review/runner.py` wants a zero-argument callable,
so bind it as `lambda: build_review_runner(document_store)`. Build it through that factory
rather than by hand: `prefilter=` is optional on `ReviewRunner` and omitting it costs
nothing visible while multiplying the per-claim model spend. · 2026-08-16

[OPEN] B1 → B3 · Persist the uploaded PDF to `{UPLOAD_DIR}/{doc_id}.pdf` ·
`app/api/routes/documents.py::upload` reads the payload into memory and hands it to
`ingest.enqueue()`, but never writes it to disk, so ADR-022's "uploads on a local volume with the
path recorded in Postgres" is not true and CP-1's upload criterion cannot be ticked. A crashed
ingest currently cannot be retried, because the bytes are gone with the worker. Compose now
mounts the `uploads` volume at `/data/uploads` and passes `UPLOAD_DIR`; `settings.upload_dir` is
already there. B1 owns neither the route nor the jobs row, so this is yours. · 2026-08-16

[OPEN] B1 → all · `anchor_fingerprints` keeps a bare table name, against the `ir_*` rule ·
`goal.md` §3 says B1's tables are `ir_*`, and `goal.md` CP-1 plus ADR-017 both name the table
`anchor_fingerprints` explicitly. Two rules in the same frozen document disagree. Resolved in
favour of the more specific one: the table stays `anchor_fingerprints`, and
`document_versions` → `ir_document_versions` (that name appears nowhere normative). Flagged
rather than silently reinterpreted; if you want the prefix everywhere, it needs an ADR and a
`make db-reset`, not a rename in passing. · 2026-08-16

[OPEN] B1 → B2 · `tests/unit/b2/test_cascade.py` fails in the full-suite run, passes alone ·
`test_a_candidate_with_no_embeddable_text_is_unjudged_not_rejected` asserts `"src_0"` and gets
`"src_11"` when the whole suite runs — a module-level id counter shared with another b2 test file
and not reset. `uv run pytest tests/unit/b2 -q` → 193 passed; `uv run pytest tests/unit -q` → that
one fails. Not B1's file to fix, and worth fixing before CI treats it as flake. · 2026-08-16

---

## 6. Checkpoint evidence

> Paste the proof when you claim a checkpoint. Test output, command output, screenshot paths.
> Format: `CP-N · <agent> · <date>` followed by evidence per acceptance criterion.

CP-4 · B2 · 2026-08-16 — **complete**

`cd services/api && uv run pytest tests/unit/b2 -q` → `193 passed in 0.64s`

| Criterion | Evidence |
|---|---|
| Three adapters behind the `Provider` protocol | `test_provider_protocol.py` — 21 passed |
| Both keys required, `MissingAPIKeyError`, no fallback | `test_key_enforcement.py` — 12 passed |
| Token-bucket limiter per provider (S2 ~1 rps, OpenAlex credit-aware) | `test_ratelimit.py` — 8 passed |
| `mailto` on every OpenAlex/Crossref call | `test_openalex_and_crossref.py` — 28 passed |
| Postgres cache on `(provider, endpoint, normalized_query_hash)` + TTL | `test_cache_keys.py` — 7 passed; `test_postgres_schema.py` — 8 passed |
| `abstract_inverted_index` inverted to plain text | `test_openalex_and_crossref.py` |
| Abstract fallback chain S2 → OpenAlex inverted → TLDR → `unavailable` | `test_semantic_scholar.py` — 22 passed |
| Provider adapters the only `source_store` writers, **enforced** | `test_source_store_hr1.py` — 17 passed (runtime guard on the writer, plus a package-wide grep test) |

CP-5 · B2 · 2026-08-16 — **complete**

| Criterion | Evidence |
|---|---|
| Atomic claims with `span_id` + `anchor_ids` + `citability` | `test_review_pipeline.py` — 41 passed; claim text is sliced from the span and dropped if the model's echoed quote disagrees with its offsets |
| All three candidate strategies live | `test_review_services.py` — 16 passed; `candidates.py` runs snippet / recommendations / OpenAlex search + one-hop concurrently |
| RRF, dedupe by DOI/S2 id, subtract everything cited | `test_review_pipeline.py` fusion tests |
| Rerank scores against **the claim**, not the topic | `test_review_pipeline.py`; unknown `source_id`s from the model are discarded, not looked up |
| Verifier returns one of the five labels | `test_review_pipeline.py` |
| Verbatim quote + mechanical substring check kills the finding | `test_review_pipeline.py` — a paraphrase and a fluent invention both die |
| One verifier, both callers | `test_review_pipeline.py` — candidate path and existing-anchor path |
| Findings stream ordered by citability desc with `verified / total` | `test_review_pipeline.py` streaming tests; B2 supplies the async generator, B3 owns the SSE framing |

Also verified this pass, and not previously true:
- **ADR-015** — every review call routes through `app/core/llm.py` by `LLMRole`;
  `test_review_pipeline.py::test_each_stage_declares_its_role_so_config_can_choose_the_model`,
  plus grep tests asserting no model SDK and no model ID anywhere under `app/review/`.
- **ADR-019** — the three prompts are files in `app/review/prompts/`;
  `test_cascade.py::test_the_three_prompts_are_files_on_disk` and the no-inline-prompt grep.
- **ADR-024** — `CITABILITY_MIN`, `RERANK_KEEP`, `VERIFY_KEEP` read from config;
  `test_cascade.py::test_thresholds_come_from_config_not_from_literals`.
- **The cascade** — embedding prefilter → rerank → verify; `test_cascade.py` — 13 passed.

`uv run ruff check app tests` → `All checks passed!`
`uv run mypy app/review app/providers` → `Success: no issues found in 27 source files`
Full backend suite: `uv run pytest tests/unit -q` → `582 passed`

CP-1 · B1 · 2026-08-16 — **NOT complete.** Two criteria are false; the rest hold.

`cd services/api && uv run pytest tests/unit/b1 -q` → `276 passed in 15.6s` (no skips; Pandoc
3.10.1 is on PATH, so the export and style tests are real rather than skipped)
`uv run ruff check app tests/unit/b1` → `All checks passed!`
`uv run mypy app/core app/ir app/parsing app/export` → `Success: no issues found in 34 source files`

| Criterion | Evidence |
|---|---|
| `docker compose up` starts api, web, grobid, postgres, redis | **Partly.** `docker compose config` validates and all five services are declared with healthchecks. Compose previously passed `ANTHROPIC_API_KEY` and no `OPENAI_API_KEY`, so the api container would have aborted on HR-2; fixed in `e1e3d64`. A full `up` against real keys has **not** been run in this session — treat as unverified. |
| Missing any of the three keys aborts startup with a clear error (HR-2) | `test_config_hr2.py` — 25 passed. Each key is asserted individually, the message names the missing key and where to obtain it, and a whitespace-only value counts as missing. |
| `app/core/` matches Appendix A exactly | `test_core_frozen.py` — 33 passed, field-by-field against the Appendix A text |
| `llm.py`: per-role routing, mandatory JSON Schema, `embed()` at 512 dims, replay raises on a miss | `test_llm.py` — 19 passed. Replay miss raises `LLMRecordingMissing` and does not construct a client; the recording key includes the model, so a model change invalidates rather than silently reusing recordings. |
| Every model ID and every threshold in `config.py` and **nowhere else** | `grep -rn "gpt-5\|text-embedding-3" app --include="*.py"` outside `config.py` → only the role-table comments in `contracts.py`, which are Appendix A verbatim. `grep -rnE "= *0\.(85\|75\|72\|55\|05\|3)\b\|2_000_000" app/ir app/parsing app/export` → 0 code hits (2 hits, both prose quoting ADR-001 in a docstring). `Arbiter` and `detect_style` no longer carry threshold defaults. |
| Per-document token budget enforced; exceeding it raises | `test_llm.py` — `TokenBudget.charge` raises `TokenBudgetExceeded`; there is no truncation path |
| `anchor_fingerprints` side table; no vector inline in the IR | `test_ir.py` — 30 passed; `CitationAnchor` has `fingerprint_id`, and a test asserts no IR field holds a `list[float]` |
| Uploads written to `/data/uploads/{doc_id}.pdf`; `jobs` table with status/error/progress | **NO.** The `jobs` half exists — B3's `agent_jobs` table carries status/error/progress. The upload half does not: `app/api/routes/documents.py::upload` never writes the payload to disk. Compose volume and `UPLOAD_DIR` are in place; the route is B3's. Filed in §5. |
| PDF → GROBID → TEI → Document IR persisted with a version number | **Partly.** TEI → IR → persist-with-version is proven (`test_services.py` — 19 passed, asserts the stored version). The GROBID leg is exercised against a stub, not a live sidecar: no real PDF has been ingested this session, so recall against a real paper is unmeasured. That measurement is CP-8 (T1). |
| IR → LaTeX renders through Pandoc without error | `test_export.py` — 32 passed, all six styles render |
| Round trip preserves title, section order, paragraph count ±0, every anchor | `test_export.py::test_round_trip_preserves_everything` and the per-style parametrisation; the negative tests confirm the check *fails* on a dropped anchor and on reordered sections, so a pass means something |

CP-2 · B1 · 2026-08-16 — **complete in code; unmeasured against real PDFs.**

| Criterion | Evidence |
|---|---|
| Every `biblStruct` → provisional CSL-JSON with a `parse_confidence` | `test_tei.py` — 28 passed, including the four known-fiddly cases named individually: name particles, `analytic` vs `monogr`, container-title, page ranges from attributes |
| Repair tier runs only below threshold and discards non-substring values | `test_repair.py` — 20 passed (invented author, expanded abbreviation, corrected typo, invented DOI, invented year, nested values); `test_segmenter.py` — 17 passed. ADR-027: any violation discards the whole entry. Note the gap this pass closed: the tier had no implementation and was wired as `segmenter=None`, so it had never run on a real upload. It now goes through `LLMClient` role `REPAIR` with a file-based prompt. |
| Arbiter: Crossref DOI → S2 match → OpenAlex, accept only at ≥ 0.85 | `test_arbiter.py` — 21 passed; ADR-025 DOI identity scores 1.0 and is labelled `doi_identity` in the breakdown |
| On accept the external record replaces our parse; raw string and our parse retained | `test_arbiter.py`, `test_pipeline.py::test_the_canonical_record_replaced_our_parse` — `provisional_csl` and `raw_string` survive on the `Reconciliation` for the audit view |
| All five tiers implemented and populated | `test_pipeline.py` — 13 passed |
| Zero references dropped (the tier invariant) | `TierCounts.assert_invariant()` runs on **every ingest**, not only in tests; `test_pipeline.py::test_the_invariant_actually_fires_when_a_reference_goes_missing` proves it fires |
| Orphan in-text markers detected and located | `test_tei.py`; each orphan carries `anchor_id`, `marker_text`, `target`, `section_id`, `span_id`, `page` |

Caveat, stated rather than discovered: every one of these runs against fixture TEI. No live model
call has been made through role `REPAIR` and there are no `LLM_MODE=record` recordings yet, so the
repair tier's *behaviour* on real reference strings is untested — only its guarantees are. Golden-set
measurement is CP-8.

CP-3 · B1 · 2026-08-16 — **complete.**

| Criterion | Evidence |
|---|---|
| Marker-family classifier (numeric vs author-date) | `test_style.py::test_marker_family_narrows_the_candidate_set` — a numeric paper scores 4 candidates, not 6 |
| Round-trip scoring through each `.csl` via Pandoc, normalised Levenshtein | `test_style.py` — 19 passed; each of the five renderable styles is recovered from strings rendered in it |
| Shortlist present in `packages/csl-styles/` | six files: `apa.csl`, `ieee.csl`, `acm-sig-proceedings.csl`, `nature.csl`, `chicago-author-date.csl`, `vancouver.csl`; `test_export.py::test_all_six_shortlisted_styles_are_present_and_readable` |
| Winning style + **numeric score** exposed via API | `GET /documents/{doc_id}/style` (B3's route) returns B1's `StyleService.detect()` payload: `style_id`, `score`, `similarity`, `margin`, per-candidate distances, and `reason` |
| Top two within 0.05 → `ambiguous`, user picks | `test_style.py::test_top_two_within_the_margin_returns_ambiguous` — `style_id` is `None` and the user's choice overrides via `select()`. The margin is `STYLE_AMBIGUOUS_DELTA` from config; `detect_style` has no default for it. |

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
