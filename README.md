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

### Requirements

Docker with ~6 GB free for the GROBID image, or for host development: Python 3.11+ with
[`uv`](https://docs.astral.sh/uv/), Node 20+ with `pnpm`, and Pandoc on `PATH`.

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
docker compose up
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

### Development on the host

Postgres, Redis, and GROBID still come from compose; the two apps run locally with reload.

```bash
docker compose up -d postgres redis grobid

# backend  → http://localhost:8000
cd services/api
uv sync
uv run uvicorn --factory app.api.main:asgi --reload --port 8000

# frontend → http://localhost:3000
cd apps/web
pnpm install
pnpm dev          # predev copies packages/csl-styles into public/csl
```

Point the backend at the sidecars with `GROBID_URL=http://localhost:8070`,
`DATABASE_URL=postgresql+asyncpg://answerthat:answerthat@localhost:5432/answerthat`, and
`REDIS_URL=redis://localhost:6379/0`.

To browse the UI with no API and no keys at all, run the frontend with
`NEXT_PUBLIC_USE_FIXTURES=1`. Fixtures are opt-in and never the default: with the flag unset and the
API down you get the configuration screen, not invented data that reads like a real review.

### Tests and checks

```bash
cd services/api
uv run pytest tests/unit -q
uv run ruff check app tests
uv run mypy app

cd apps/web
pnpm typecheck && pnpm lint && pnpm build
```

The export and style-detection tests need Pandoc on `PATH`. CI is meant to run with
`LLM_MODE=replay` so a missing recording fails the build rather than quietly hitting the network.

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

## License

MIT — see [LICENSE](LICENSE).
