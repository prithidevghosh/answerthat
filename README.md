# answerthat

Upload a paper as a PDF, get a peer review grounded in real academic search, then edit the paper by
natural-language instruction — while every citation in it survives intact.

The first screen is an upload, not a landing page.

```
PDF ──► GROBID ──► Document IR ──► arbiter (Crossref / S2 / OpenAlex) ──► review ──► edit ──► .tex
             TEI          versioned          real records only              streamed   kernel-checked
```

---

## What it does

1. **Parse.** GROBID turns the PDF into TEI; TEI becomes a purpose-built Document IR with sections,
   spans, and in-text citation anchors. Every `biblStruct` becomes provisional CSL-JSON with a
   parse confidence.
2. **Arbitrate.** Each reference is reconciled against a real external record — Crossref by DOI,
   then Semantic Scholar `/paper/search/match`, then OpenAlex. A match is accepted only at
   `agreement_score ≥ 0.85`, and on accept the external record replaces our parse as canonical.
   Everything that does not resolve lands in a visible tier, never in a silent drop.
3. **Detect style.** Marker-family classification narrows the shortlist; each candidate `.csl` is
   rendered through Pandoc and scored by normalised Levenshtein against the raw reference strings we
   actually extracted. The winning style is shown with its numeric score.
4. **Review.** Atomic claims are extracted with citability scores, candidates are retrieved by three
   strategies and fused, reranked against the claim, then verified with a verbatim quote that is
   mechanically substring-checked against the fetched abstract. Findings stream over SSE as they
   verify.
5. **Edit.** A planner emits a typed `EditPlan` as structured output; an invariant kernel — pure
   code, no model call — accepts, flags, or rejects it; approved changes commit a new revertible IR
   version under optimistic locking.
6. **Export.** IR renders to LaTeX through Pandoc with the detected CSL style.

There are two ways through those steps. The **deterministic** flow gives each one a screen and the
user drives. The **conversational** flow (ADR-031…034) puts an agent above the same functions: it
answers questions about the paper, describes what a review will actually do before running one,
proposes edits, and commits only after the user has answered — with every orphaned citation anchor
decided one at a time. Nothing about the guarantees changes between them; the kernel, the source
store and the approval gate are the same code.

## The five rules the code is built around

These are load-bearing, not aspirational. `goal.md` states them; `decision.md` carries the ADRs.

| | Rule | How it is enforced |
|---|---|---|
| **HR-1** | No fabricated sources, structurally | `source_id` is a foreign key into an append-only `source_store` that only `app/providers/*` may write to, and only from a real HTTP response |
| **HR-2** | Fail fast on credentials that would degrade silently | `OPENALEX_API_KEY`, `OPENALEX_MAILTO`, `OPENAI_API_KEY` are required; the app raises on startup if any is absent |
| **HR-3** | Failures are surfaced, never swallowed | Every failure mode — quarantined reference, orphan marker, missing abstract, low-confidence reattachment — has a designed visible state |
| **HR-4** | CSL is the only citation model | Pandoc on the backend, `citation.js` in the frontend, both reading the same files in `packages/csl-styles/`. Zero hand-written citation formatting |
| **HR-5** | Citations survive every edit | Detach → transform → reattach. The text model never sees a citation marker; an anchor that cannot be reattached is raised to the user, never dropped |

---

## Running it

Everything runs in Docker. The only requirement is Docker with ~6 GB free for the GROBID image —
no Python, Node, or Pandoc on your machine.

### 1. Configure

```bash
cp .env.example .env
```

Fill in the three required keys. **The API will not start without them** — that is HR-2, not a bug.

| Variable | Required | Notes |
|---|---|---|
| `OPENALEX_API_KEY` | **yes** | Free, register at [openalex.org](https://openalex.org) |
| `OPENALEX_MAILTO` | **yes** | Your contact email — the polite pool. Outside it, throttling looks like sparse results rather than an error |
| `OPENAI_API_KEY` | **yes** | Every LLM call and every embedding routes through this one key |
| `SEMANTIC_SCHOLAR_API_KEY` | no | Blank means the shared unauthenticated pool: slower and burstier, never silently thinner, because S2 throttles with a 429 we raise on |
| `LLM_MODE` | no | `live` (default), `record`, or `replay`. In `replay` a cache miss raises instead of reaching the network |
| `GROBID_URL`, `DATABASE_URL`, `REDIS_URL`, `UPLOAD_DIR` | no | Default to the compose service names |

### 2. Start

```bash
docker compose up --build
```

Brings up five services:

| Service | Port | Notes |
|---|---|---|
| `web` | 3000 | Next.js — **open this** |
| `api` | 8000 | FastAPI; docs at `/docs`, health at `/api/health` |
| `grobid` | 8070 | 1–3 GB image, takes 30–60 s to become healthy on a cold start |
| `postgres` | 5432 | IR, source store, provider cache, jobs |
| `redis` | 6379 | Job queue |

The first boot waits on GROBID's healthcheck, so give it a minute before the API reports ready.
Every port is overridable (`API_PORT`, `WEB_PORT`, `GROBID_PORT`, `POSTGRES_PORT`, `REDIS_PORT`) if
something on your machine already owns it.

Then open **http://localhost:3000** and drop in a PDF.

### 3. Everyday commands

```bash
docker compose logs -f api        # follow the backend
docker compose up --build -d      # rebuild and run detached
docker compose down               # stop
docker compose down -v            # stop, and wipe the database and uploaded PDFs
```

`services/api/app/` and `packages/csl-styles/` are mounted into the containers, so editing a source
file or a `.csl` is picked up without a rebuild. Rebuild when a dependency changes.

### Tests and checks

They run inside the api container, which already has Pandoc and the dev tooling installed:

```bash
docker compose exec api pytest tests/unit -q
docker compose exec api ruff check app tests
docker compose exec api mypy app
```

CI is meant to run with `LLM_MODE=replay`, so a missing recording fails the build rather than
quietly hitting the network.

---

## Repository layout

```
├── goal.md                       the contract — what "finished" means
├── decision.md                   the ADRs — why every choice is what it is
├── memory.md                     working memory: gotchas, learnings, checkpoint evidence
├── system-design.md              locked architecture
├── docker-compose.yml .env.example
├── apps/web/                     Next.js 15 · App Router · upload, parse inspector,
│                                 review feed, edit console, export
├── packages/csl-styles/          the one copy of each .csl, read by Pandoc and citation.js
└── services/api/app/
    ├── core/                     contracts, config (every model ID and threshold),
    │                             LLM client with per-role routing and record/replay
    ├── ir/                       Document IR, versioned store, anchor fingerprints
    ├── parsing/                  GROBID → TEI → IR, CSL-JSON, repair tier, arbiter, style
    ├── providers/                Semantic Scholar · OpenAlex · Crossref, rate limiting,
    │                             response cache, the only writers to source_store
    ├── review/                   claims → candidates → fusion → rerank → verify → stream
    ├── agent/                    planner, typed operations, invariant kernel, diff, versioning
    ├── orchestrator/             the conversational flow: tool registry, agent loop,
    │                             confirmation gate, conversations, evidence index
    ├── export/                   IR → Pandoc → LaTeX
    └── api/                      FastAPI routes, jobs, SSE
```

Model IDs and thresholds live in `app/core/config.py` and nowhere else. Prompts are files inside
their own package (`parsing/prompts/`, `review/prompts/`, `agent/prompts/`), never inline strings.

## API surface

Everything is mounted under `/api`; interactive docs at `http://localhost:8000/docs`.

| Method | Path | |
|---|---|---|
| `POST` | `/api/documents` | Upload a PDF → `202` with a job handle |
| `GET` | `/api/documents/{id}/parse-status` · `/parse` | Ingest progress; the full parse report with every confidence tier |
| `GET` | `/api/documents/{id}` · `/versions` · `POST /revert` | The IR at head or at a version |
| `GET`/`PUT` | `/api/documents/{id}/style` | Detected style with its score; override when ambiguous |
| `POST` | `/api/documents/{id}/review` | Start a review → `202` |
| `GET` | `/api/documents/{id}/review/stream` | SSE findings, ordered by citability, with `verified / total` |
| `POST` | `/api/documents/{id}/commands` | Natural-language edit → a proposed change set |
| `POST` | `/api/change-sets/{id}/approve` | Per-change approve/reject + orphan decisions, against a `base_version` |
| `GET` | `/api/documents/{id}/export.tex` · `/export/manifest` | The revised LaTeX |
| `GET` | `/api/sources/{id}` · `/api/jobs/{id}` · `/api/agent/metrics` | Source record, job state, `FreeformEdit` firing rate |
| `POST` | `/api/documents/{id}/chat` | Create or return this document's conversation |
| `GET`/`POST` | `/api/chat/{id}` · `/messages` · `/stop` | The transcript; send a message → `202`; cancel the in-flight turn |
| `GET` | `/api/chat/{id}/stream` | SSE — replays the event log, then follows live |

Connect the browser's `EventSource` **directly** to the FastAPI SSE endpoint — proxying it through a
Next.js route buffers the stream and makes findings arrive in a clump.

---

## Status

The parsing, provider, review, agent, and API layers are implemented and covered by the unit suites
under `services/api/tests/unit/{b1,b2,b3}`; the frontend implements every screen and failure state.
What is **not** yet done is the verification checkpoint (CP-8): the golden set of real arXiv PDFs,
the reported parsing metrics, committed `LLM_MODE=replay` recordings, and the threshold sweep for
`ARBITER_ACCEPT` / `REPAIR_TRIGGER` / `REATTACH_ACCEPT`. Those thresholds are currently the values
argued for in ADR-024, not values measured against a corpus. `memory.md` §6 records the per-checkpoint
evidence, including what each one does and does not prove.

## Known limitations

Roughly in the order they would bother me if someone else were running this.

- **Nothing is measured against real papers.** The unit suites prove the *guarantees* — a fabricated
  `source_id` is rejected, a quote that isn't in the abstract kills the finding — but there is no
  accuracy number anywhere. Reference recall, arbiter resolution rate and reattachment quality are
  all unknown, and `ARBITER_ACCEPT = 0.85` / `REATTACH_ACCEPT = 0.72` are arguments from ADR-024,
  not values swept against a corpus.
- **No model call has ever been recorded.** Every test runs against scripted fakes, so
  `LLM_MODE=replay` has nothing to replay and CI can't use it yet. The parts that depend on model
  judgement rather than on code — whether the planner picks the right typed operation, how the
  repair tier behaves on a genuinely mangled reference string — are untested.
- **Jobs run inside the API process.** `arq` and Redis are wired and record job status, but ingest
  and review actually run on `asyncio.create_task`. That means one process only, and a restart kills
  work that is still in flight. What a restart no longer destroys is *finished* work: parse reports
  are persisted to `parse_reports` and conversations to the `chat_*` tables (ADR-032), so `/parse`
  and the chat both survive one. A review's findings still live in the runner's memory and are gone
  after a restart; re-running one is a second billed pass.
- **Retrieval is one strategy short without a Semantic Scholar key.** S2 serves search and batch
  from a pool that is closed to anonymous callers, not merely slow (ADR-010b), so a review runs
  three candidate strategies instead of four and loses passage-level evidence. Findings are still
  quote-backed; ranking is just worse. Keys are not currently being issued.
- **Figures, tables and equations don't survive export.** They come out as visible placeholders
  carrying their captions. Deliberate (ADR-008), but it means the exported `.tex` is not a drop-in
  replacement for the original manuscript.
- **Ranking reads the stored abstract directly.** If S2 had no licensed abstract, the record holds
  its TLDR and the prefilter and reranker score the one-liner rather than OpenAlex's fuller text.
  A ranking cost, not an honesty one — the verifier still reads the resolver's best abstract.
- **Single user, no authentication** (ADR-023). Anyone who can reach the API can read and edit every
  document.
- **No migrations.** Tables come from `create_all()` at startup (ADR-020); the one schema change so
  far was a hand-applied `ALTER TABLE`.
- **No cost or latency figure.** There is a per-document token budget that raises when exceeded, but
  nobody has written down what a full review of a real paper costs or how long it takes.
- **The frontend has no tests** beyond typecheck, lint and build.

## With more time

In this order, because the first item makes the rest honest.

1. **Build the golden set.** 5–8 real arXiv PDFs across IEEE, APA, ACM and Nature superscript,
   hand-checked, with reference recall, field precision, arbiter resolution rate and mean round-trip
   style similarity reported as numbers. Then sweep the three thresholds against it. Until this
   exists, every quality claim in this README is a design argument rather than a result.
2. **Record the model calls** and make CI run `LLM_MODE=replay`, so a cache miss fails the build
   instead of quietly reaching the network.
3. **Move ingest and review onto `arq` properly** and persist the parse report in Postgres, so a
   restart is survivable and review can scale past a single process.
4. **Route the prefilter and reranker through `AbstractResolver`** instead of reading
   `record.abstract`, which fixes the TLDR ranking cost above.
5. **Show provider disagreements in the UI.** The store already keeps them (ADR-028) and nothing
   displays them — "these two databases describe this work differently" is exactly what a reviewer
   wants to see.
6. **End-to-end tests**: upload → parse → review → edit → approve → export, against recorded
   fixtures, plus a frontend suite worth the name.

## License

MIT — see [LICENSE](LICENSE).
