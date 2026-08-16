# decision.md — Architecture Decision Log

Every design decision on this project, with its reasoning. **This is a living document.**

## Rules for agents

- **Read this before writing code.** It explains why the constraints in `goal.md` exist. Constraints
  you don't understand are constraints you'll accidentally violate.
- **If you change a decision, add a new ADR. Never edit an accepted one in place.** Set the old one
  to `Superseded by ADR-NNN` and write the new one with its own reasoning.
- **If you find a decision is wrong**, do not work around it silently. Write the counter-evidence
  into `memory.md` under Blockers, propose an ADR, and stop.
- ADR numbers are permanent. Never reuse one.

Status values: `Accepted` · `Superseded by ADR-NNN` · `Proposed` · `Rejected`

---

## ADR-001 — Layered parsing cascade with an external arbiter

**Status:** Accepted

**Context.** A PDF must become structured sections plus parsed, normalized citations. Options
considered: (a) LLM-native extraction from page images, (b) GROBID alone, (c) a layered cascade
whose output is reconciled against external bibliographic APIs.

**Decision.** (c). GROBID primary → constrained LLM repair for low-confidence entries → **arbiter**
reconciling every entry against Crossref (by DOI), Semantic Scholar `/paper/search/match` (by title),
then OpenAlex.

**Reasoning.** The arbiter is the load-bearing idea. "Did we parse this correctly?" is unanswerable
without the ground truth we're trying to produce. "Does this resolve to a real record that agrees
with what we extracted?" is answerable, and it yields three things at once: a self-healing parse
(a mediocre parse that matched the right paper is replaced by clean external metadata), a real
linkable URL per reference, and a principled definition of "unparseable" for the quarantine bucket
instead of a hand-tuned confidence cutoff.

LLM-native extraction was rejected outright: it silently "repairs" a mangled reference into a
plausible one, which is fabrication at the ingestion layer, and it never reports failure — making
HR-3 unimplementable.

**Consequences.** More moving parts and API budget at ingest. Mitigated by batching, `/paper/batch`
hydration, and a persistent cache.

---

## ADR-002 — AnyStyle dropped from the cascade

**Status:** Accepted (amends ADR-001)

**Context.** The original cascade had three parse tiers: GROBID → AnyStyle → LLM repair.

**Decision.** Two tiers. GROBID → constrained LLM repair → arbiter. No AnyStyle.

**Reasoning.** GROBID is Java, AnyStyle is Ruby, our service is Python. Three runtimes in one
container for a tier that only sees entries GROBID already flagged low-confidence — and the arbiter
downstream recovers much of what tier 2 would have fixed anyway. The cost/benefit doesn't hold.

**Revisit if:** telemetry shows the LLM repair tier failing frequently on entries the arbiter then
also fails to resolve.

---

## ADR-003 — Constrained repair tier: no token may be invented

**Status:** Accepted

**Decision.** The LLM repair tier may only **segment and label the literal characters present** in
the raw reference string. A mechanical post-check discards any emitted field value that is not a
substring of the raw string (after whitespace/punctuation normalisation); the entry is then marked
unparsed rather than accepted.

**Reasoning.** This is the difference between an LLM used as a parser and an LLM used as a
generator. A parser that can invent an author name is a fabrication engine wearing a parser's
clothes. The substring check is mechanical — no prompt can talk past it.

---

## ADR-004 — Purpose-built Document IR; LaTeX is a render target only

**Status:** Accepted

**Context.** Options: (a) edit LaTeX source as text, (b) use the Pandoc AST as the IR, (c) a
purpose-built IR.

**Decision.** (c). Sections → blocks → spans, with **citation anchors as first-class nodes carrying
stable IDs**, plus an append-only `source_store`. LaTeX/Pandoc is an output renderer, never the
working representation.

**Reasoning.** This choice is what makes every other guarantee enforceable. "The citation multiset
is preserved" is not a checkable statement about a LaTeX string; it is trivially checkable about a
typed structure. Editing LaTeX text means citation loss can only be detected *after* the damage,
by diffing key sets. The Pandoc AST was rejected because we cannot hang our own metadata
(confidence, provenance, claim links, verification) on its nodes without a position-keyed side table
— and positions move exactly when we edit, which is precisely where our hardest requirement lives.

**Consequences.** We own the IR, the TEI→IR mapping, and the IR→Pandoc mapping. Worth it.

---

## ADR-005 — Claim-first semantic retrieval, not section keyword search

**Status:** Accepted

**Decision.** The unit of review is an **atomic claim**, not a section. Candidates come from three
strategies in parallel: S2 `/snippet/search` (passage-level evidence), **S2 Recommendations seeded
with the paper's own cited works** (SPECTER2 embedding space), and OpenAlex search plus one-hop
`cited_by`/`references` expansion from the existing bibliography. Fuse by reciprocal rank, dedupe by
DOI/S2 id, subtract everything already cited, then rerank **against the claim**, not the topic.

**Reasoning.** Keyword search over section text returns topically adjacent papers, not the work the
author should have cited — and the brief explicitly calls out "keyword search dressed up as semantic
search" as a failure. The Recommendations API is the only genuinely semantic signal either provider
gives us for free, and seeding it with the paper's own bibliography is a direct expression of the
question "what did this literature neighbourhood contain that they missed?"

---

## ADR-006 — Quote-backed entailment with a mechanical substring check

**Status:** Accepted

**Decision.** Verification returns one of `supports`, `partially_supports`, `does_not_address`,
`contradicts`, `unverifiable_no_abstract`, and every non-`unverifiable` verdict **must carry a
verbatim quote from the fetched abstract**. A substring check kills the finding if the quote is not
actually present. If no abstract can be retrieved through the fallback chain (S2 → OpenAlex inverted
index → S2 TLDR), `unverifiable_no_abstract` is the only legal output and it is **displayed**.

**Reasoning.** Same principle as ADR-003 in a different place: make the honesty property mechanical
rather than prompted. A verifier that must quote its evidence cannot fabricate support for a claim
without producing a string we can check against the source text.

One verifier serves both review tasks — pointed at candidates it finds missing work; pointed at
existing anchors it checks whether cited sources support their claims. Identical code, identical
evidence format.

---

## ADR-007 — Planner → typed operations → invariant kernel

**Status:** Accepted

**Context.** Options: (a) single ReAct loop with edit tools, (b) multi-agent reviewer/editor/critic
debate, (c) a planner emitting typed operations, executed deterministically, gated by a pure-code
invariant kernel.

**Decision.** (c).

**Reasoning.** In (a) the model *decides whether* to preserve citations; preservation becomes a
matter of prompt and luck, and failures are unattributable. (b) was rejected because a critic is a
**probabilistic check on a problem that admits a deterministic one** — cost and non-determinism
multiply for a weaker guarantee. In (c) the kernel is pure code: it can be unit-tested without a
model, every rejection is explainable, and fabrication is excluded by the type system rather than
discouraged by instruction.

**Consequences.** A closed operation vocabulary constrains what users can ask for — addressed by
ADR-009.

---

## ADR-008 — Export fidelity: text and citations exact, figures/tables/equations as placeholders

**Status:** Accepted

**Decision.** Sections, paragraphs, citations, and the bibliography survive the LaTeX round trip
exactly. Figures, tables, and equations become visible placeholder blocks carrying their captions.

**Reasoning.** Table reconstruction from PDF is its own research problem and would consume days
better spent on the agent and the parsing guarantees, which are what the work is actually judged on.
This is a **stated** scope cut, not a discovered one — the placeholder is visible in the export so
no user mistakes it for fidelity we don't have.

**Revisit:** equations first (via GROBID formula extraction) if time allows — a paper with no
equations reads as broken.

---

## ADR-009 — `FreeformEdit` escape hatch, gated

**Status:** Accepted

**Context.** A closed operation vocabulary refuses instructions it cannot type, e.g. *"reframe my
contribution as being about efficiency rather than accuracy, and make the abstract and intro agree"*.
Honest, but the first wall a real user hits.

**Decision.** Include `FreeformEdit(target, instruction)` as a seventh operation. It runs through the
**same invariant kernel** — no invented sources, citation multiset preserved, orphaned anchors
surfaced — but only the generic invariants apply, since the kernel cannot reason about an edit type
it doesn't know. The planner must emit `no_typed_op_applies=True` plus a justification to select it,
and its firing rate is logged.

**Reasoning.** Citation safety is preserved either way; what we lose is *tailored* checking and a
crisp change explanation. The real risk is planner laziness — an operation that always applies will
attract every command, leaving `Shorten` as dead code. The gate plus the metric make that visible.

**Tripwire:** if `FreeformEdit` exceeds ~20% of commands, the typed vocabulary is wrong. **Fix the
vocabulary, do not lean on the hatch.**

---

## ADR-010 — Fail fast on missing API keys. No anonymous or degraded mode.

**Status:** Accepted, **amended by ADR-010a** (Semantic Scholar's key is now optional)

**Context.** OpenAlex moved to mandatory keys and credit-based limits on 13 Feb 2026 — anonymous
access is 100 credits/day, and a list query costs 10 credits, i.e. roughly ten searches per day.
Semantic Scholar throttles unauthenticated traffic in a shared pool. The initial design proposed
running anonymously with a "degraded coverage" banner.

**Decision.** Both `SEMANTIC_SCHOLAR_API_KEY` and `OPENALEX_API_KEY` are **required**. The
application raises on startup if either is absent or empty. Provider constructors raise
`MissingAPIKeyError`. There is no anonymous path, no default route, no silent fallback.

**Reasoning.** A degraded mode is the most dangerous failure this system can have. Under anonymous
limits, searches don't error — they return *thin or empty results*, which the review pipeline would
faithfully report as **"no missing work found"**. That is a false negative dressed as a clean bill of
health: the exact failure mode HR-3 exists to prevent, and worse than an outright crash because it
is invisible. A rate-limited empty result and a genuinely empty result are indistinguishable
downstream, so the only safe design is to make the misconfiguration impossible to run.

**Consequences.** No zero-config demo. `.env.example` documents both keys and `README.md` links to
where each is obtained. This is the correct trade.

---

## ADR-010a — `SEMANTIC_SCHOLAR_API_KEY` is optional. ADR-010 was right about the invariant and wrong about S2.

**Status:** Accepted. Amends ADR-010.

**Context.** ADR-010 required both academic keys on one premise: *"under anonymous limits, searches
don't error — they return thin or empty results."* That premise was asserted for both providers and
verified for neither. Checked against the APIs, it holds for exactly one of them:

| | Anonymous behaviour | Reaches the pipeline as |
|---|---|---|
| **OpenAlex** | 100 credits/day, a list query costs 10 → ~10 searches, then thin results | a plausible empty literature — **silent** |
| **Semantic Scholar** | shared unauthenticated pool; over it, **HTTP 429** | `ProviderRateLimited`, raised — **loud** |

`http.py` already retries a 429 with backoff, honours `Retry-After`, and then *raises* rather than
returning `[]` — with a comment citing this very ADR. So for S2 the invariant ADR-010 exists to
protect was already enforced on the response path, and the startup gate was protecting nothing.

Second fact, discovered at the same time: since **2024-09** Semantic Scholar approves no key
requests from free-domain email addresses and none for third-party applications. The abort message
told operators to "request at semanticscholar.org/product/api" — for most of them, a dead end. A
gate that buys no safety and cannot be satisfied is worse than no gate.

**Decision.** `SEMANTIC_SCHOLAR_API_KEY` is optional. Present, it is sent as `x-api-key` and buys a
dedicated ~1 RPS; absent, S2 is called anonymously and the header is omitted entirely.
`SemanticScholarProvider` calls `optional_key()` instead of `require_key()`. `OPENALEX_API_KEY`,
`OPENALEX_MAILTO` and `OPENAI_API_KEY` are unchanged and still startup-fatal.

**Reasoning.** The invariant was never "every provider has a key" — it was *a throttled search must
never reach the pipeline disguised as an empty literature*. Requiring a key is one way to enforce
that, and the weaker one: it acts at startup on a proxy for the risk. Raising on 429 acts at the
moment the risk actually materialises, and keeps working whether or not a key is set. Where the
strong enforcement is available we take it; where the provider fails loudly we do not need it.

**Consequences.** The unauthenticated pool is shared globally, so throughput is contended and
bursty where a keyed 1 RPS is steady. That surfaces as slower reviews and occasional
`ProviderRateLimited`, both visible — which is the trade ADR-010 would have made had the premise
been checked. Startup logs which providers are unauthenticated (`unauthenticated_providers()`), and
`snapshot()` reports `authenticated` per provider: HR-3 applies to configuration too, so the regime
is stated rather than inferred from latency.

**Tripwire:** if `ProviderRateLimited` from S2 becomes routine rather than occasional, the shared
pool is no longer adequate and the answer is a key or a lower request rate — **not** a `return []`
on 429. That branch is the failure ADR-010 was written to prevent, and it stays prohibited.

**The rule this generalises to.** Before requiring a credential, ask *how does this API tell us it
is throttling us?* Silent thinning ⇒ require it. An error status we already raise on ⇒ optional.

---

## ADR-010b — Unauthenticated, S2's search pool is closed, not slow. Drop the strategy; say so.

**Status:** Accepted. Amends ADR-010a. Tripped by ADR-010a's own tripwire.

**Context.** ADR-010a kept the key optional on the premise that anonymous access is "contended and
bursty" — slower, with "occasional `ProviderRateLimited`". It set a tripwire: *if that becomes
routine rather than occasional, the answer is a key or a lower request rate — not a `return []`.*

It became routine. Measured 2026-08-16 from an idle machine, six calls per endpoint two seconds
apart:

| Closed | | Open | |
|---|---|---|---|
| `/snippet/search` | 0/6 | `/paper/search/match` | 6/6 |
| `/paper/search` | 0/6 | `/paper/{id}/references` | 6/6 |
| `/paper/batch` | 1/6 | `/paper/{id}/citations` | 5/6 |
| `/paper/{id}` | 1/6 | `/recommendations/v1/papers` | 6/6 |

S2 serves *search and batch* from a different pool than the rest of the Graph API. Both remedies
ADR-010a allowed are unavailable: a key cannot be obtained (per ADR-010a's own finding, and
confirmed — the one key we held now answers 403), and "a lower request rate" cannot help because a
**single cold request** answers 429. S2 sends no `Retry-After`, so each attempt cost four retries
and ~7s of backoff before raising.

The consequence was worse than slowness. `candidates.py` gathers its strategies without
`return_exceptions=True` — deliberately, so a throttled strategy cannot masquerade as a thin
literature — so the first claim of every review died. `abstracts.py` propagates for the same
reason, so a 429 at step 1 killed the resolve *before* OpenAlex, losing the step that would have
answered. An optional key was behaving like a required one.

**Decision.** Endpoints on S2's search pool are gated by `search_pool_available`, which is
`authenticated`. Callers ask before running: `CandidateGenerator` omits `s2_snippet` from the
strategy set, `AbstractResolver` skips steps 1 and 3 so OpenAlex leads. Calling a gated endpoint
anyway raises `ProviderEndpointUnavailable` before any HTTP — a distinct type, deliberately **not**
a `ProviderRateLimited` subclass, so "we did not ask" stays separable from "we asked and were
throttled". The reduction is reported: `strategies_for()`, a `retrieval_configured` progress event,
and `AbstractResult.skipped`.

This amends ADR-010a's "`authenticated` … reported, never branched on". That line assumed anonymous
access works. There is now exactly one branch, and it selects *which endpoints to call* — never what
to do with a response.

**Reasoning.** ADR-010's invariant is that a throttled search must never reach the reader as an
empty literature. Skipping a declared-unavailable strategy does not violate it, because nothing is
disguised: the system states which strategies ran, so a narrower search is legible as a narrower
search rather than as a clean paper. The prohibited branch — `except ProviderRateLimited: return []`
— remains prohibited, and an *available* strategy that raises still takes the review with it.

**Consequences.** Without a key the review runs three strategies instead of four. What is lost is
passage-level evidence: `matched_passage` is null, and the prefilter and reranker lose snippet
context, so ranking is somewhat worse. What is **not** lost is any finding's basis — ADR-006's
verbatim quote comes from the fetched abstract, and the verifier never read snippets. ADR-005's
"only genuinely semantic signal", bibliography-seeded Recommendations, is on the pool that answers
anonymously and is untouched, as is the arbiter's `match_reference`.

**Tripwire, replacing ADR-010a's.** If the *open* column starts failing too, the shared pool is gone
entirely and S2 becomes OpenAlex-only for retrieval — a re-measurement, not a `return []`. And if a
key is ever obtained, nothing needs unwinding: `search_pool_available` becomes true and the fourth
strategy comes back on its own.

---

## ADR-011 — Style detection by round-trip scoring

**Status:** Accepted

**Decision.** Classify the marker family from in-text markers (numeric vs author-date) to narrow the
candidate set, then render our reconciled CSL-JSON through each shortlisted `.csl` via Pandoc,
compare each rendering to the **raw reference strings we actually extracted** by normalised
Levenshtein, and take the argmin. Show the score. Top two within 0.05 → declare ambiguous and let
the user pick.

**Reasoning.** Deterministic and explainable, versus asking a model and getting an unverifiable
answer. It degrades into "we're not sure, you pick" exactly when it should. It also doubles as a
regression test for the entire parse pipeline — mean round-trip similarity drops before anything
else visibly breaks.

---

## ADR-012 — Stack: FastAPI + Next.js, GROBID as a Docker sidecar

**Status:** Accepted

**Decision.** Python/FastAPI backend, Next.js frontend, Postgres, Redis, GROBID as a Docker sidecar
on :8070. Monorepo.

**Reasoning.** The backend needs async fan-out under rate limits, `lxml` for TEI, and Pandoc — all
natural in Python. The split gives us **the right citeproc in each place**: `citation.js` in the
frontend for live preview, Pandoc on the backend for authoritative export, **both reading the same
`.csl` files from `packages/csl-styles/`**, so preview and export cannot drift.

GROBID as a sidecar over a hosted instance: reproducible, offline-capable, no dependency on a third
party's uptime during a demo. GROBID's own default of **linear-chain CRF** (over the DeLFT
deep-learning variants) is kept — their docs are candid that transformers don't yet beat CRF on
accuracy-per-cost for these tasks. DL remains a per-model upgrade for the `citation` model alone if
telemetry identifies it as the bottleneck.

---

## ADR-013 — Detach → transform → reattach

**Status:** Accepted

**Decision.** Text transforms never regenerate text with citations inline. The sequence is:
(1) detach anchors, recording each one's `context_fingerprint` (an embedding of its host sentence);
(2) compress or rewrite the **text only** — the model never sees or emits citation markers;
(3) reattach each anchor by scoring its fingerprint against the new sentences, attaching at argmax
above threshold; (4) any anchor below threshold is **raised to the user as a decision**
(keep here / move to… / remove), never dropped.

**Reasoning.** This is the mechanism behind HR-5. "Citations stay attached to the right context when
text moves or shrinks" cannot be achieved by asking a model to be careful while rewriting a
paragraph containing `[12]`. Removing citations from the model's view during rewriting makes losing
them impossible, and step 4 converts the residual hard cases from silent deletion into visible
user choices.

---

## ADR-014 — Streaming background review, ordered by citability

**Status:** Accepted

**Context.** Claim-first review at ~1 req/s on Semantic Scholar means a 40-claim paper takes 5–8
minutes. That floor is set by the rate limit, not by our code.

**Decision.** Review runs as a background job; findings stream to the UI over SSE as each verifies,
ordered by **citability descending** so the most consequential findings arrive first. Progress shows
`verified / total`.

**Reasoning.** Nobody watches a spinner for six minutes; everybody watches findings appear. Ordering
by citability means the first thirty seconds carry the most value. Section-scoped review was kept as
a *user* option for speed, but full-paper streaming is the default because partial coverage
presented as a review is the dishonesty HR-3 exists to prevent — and the `verified / total` counter
makes in-progress state unmistakable.

---

## ADR-015 — OpenAI as the LLM provider, with per-role model routing

**Status:** Accepted
**Supersedes:** nothing — this fills a gap that should never have existed. The original design
specified six distinct LLM roles without ever naming a provider or a model.

**Decision.** All LLM calls go to **OpenAI**, through a single client in `app/core/llm.py`. Models
are selected **per role**, never globally, and every model ID is pinned in one config module — no
model string appears anywhere else in the codebase.

| Role | Model | Why |
|---|---|---|
| Reference repair (ADR-003) | `gpt-5.4-mini` | Mechanical segment-and-label. Cheap, high volume, guarded by a substring check anyway. |
| Claim extraction (ADR-005) | `gpt-5.4` | Structured decomposition over long sections. Quality matters, volume is per-section. |
| Candidate rerank | `gpt-5.4-mini` | Only after an embedding prefilter has cut the field to ~10. Highest call volume in the system. |
| **Verification (ADR-006)** | **`gpt-5.5`** | **Accuracy-critical.** This is the judgment the whole product's honesty rests on — do not economise here. |
| Planner (ADR-007) | `gpt-5.5` | Command → typed plan. Low volume, high consequence, structured output. |
| Text transform (ADR-013) | `gpt-5.4` | Rewriting the user's own prose. Quality is visible to the researcher. |

**Reasoning.** A single model for everything is wrong in both directions: frontier models for
reference-string segmentation is waste, and a cheap model for entailment verification is the one
place a mistake becomes a false claim shown to a researcher as fact. The rerank stage is the volume
hotspot (claims × candidates), so it gets an embedding prefilter first and a mini model second.

Structured output is used for **every** role that returns data — JSON Schema via the Responses API,
not prompt-and-parse. The planner in particular *cannot* emit prose by construction (ADR-007), and
schema-enforced output is how that's true rather than hoped.

Context windows are ~1M tokens, so whole-section and whole-document prompts fit without chunking.
Do not build a chunker we don't need.

**Consequences.** `OPENAI_API_KEY` is required at startup on the same terms as the academic APIs
(HR-2). A per-document token budget is enforced; exceeding it raises and surfaces rather than
silently truncating the review.

---

## ADR-016 — Embeddings: OpenAI `text-embedding-3-small` at 512 dimensions

**Status:** Accepted

**Context.** `CitationAnchor.context_fingerprint` is central to ADR-013 — detach/reattach scores an
anchor's recorded sentence context against the rewritten text. **The original contract declared the
field and never specified what produces it.** The reattachment mechanism, and therefore HR-5, had no
implementation path.

**Decision.** `text-embedding-3-small` with the `dimensions` parameter set to **512**. Same vendor as
the LLM, so one key and one client.

**Reasoning.** This is same-document sentence similarity — matching "the sentence this citation used
to live in" against a handful of candidate sentences in the rewritten paragraph. It is a genuinely
easy retrieval task over a tiny candidate set; `-3-large` at 3072 dimensions buys nothing here and
costs 6.5× more and 6× the storage. If reattachment accuracy proves insufficient in testing, the
upgrade path is a one-line config change — but measure before spending.

The same embeddings serve the rerank prefilter, cutting candidates to ~10 before any LLM sees them.

---

## ADR-017 — Fingerprints live in a side table, not inline in the IR

**Status:** Accepted
**Amends:** the Appendix A contract in `goal.md`

**Context.** `context_fingerprint: list[float]` sat inline on every `CitationAnchor`. With
copy-on-write versioning (ADR-004), every document version would duplicate every vector.

**Decision.** `CitationAnchor` carries `fingerprint_id: str | None`. Vectors live in an
`anchor_fingerprints` table keyed by that id. Fingerprints are **immutable and shared across
versions** — an anchor's recorded context doesn't change when an unrelated paragraph is edited.

**Reasoning.** Storage is the lesser argument (~245 KB per version at 60 anchors). The real one is
**diff legibility**: structural diffs are the mechanism by which a user verifies HR-5 with their own
eyes, and a diff where every citation anchor renders as 512 floats is unreadable. The design's whole
approval story depends on that diff being human-scannable.

---

## ADR-018 — LLM calls are not deterministic; tests replay recordings

**Status:** Accepted

**Decision.** Do not attempt to make LLM output reproducible via temperature or seeds. Instead,
`app/core/llm.py` has a **record/replay layer**: `LLM_MODE=record` writes every request/response pair
keyed by a hash of (role, model, prompt, schema); `LLM_MODE=replay` serves from those recordings and
**raises on a cache miss** rather than calling the API.

**Reasoning.** Reasoning models don't honour temperature the way older ones did, and a CI suite that
silently re-runs live LLM calls is both slow and non-reproducible. Replay-with-hard-miss is the only
honest option — a missing recording is a test that must be re-recorded deliberately, not a test that
quietly hits the network.

This also gives T1 deterministic e2e runs, and it is the same pattern as the recorded provider
fixtures for the academic APIs (CP-8).

---

## ADR-019 — Prompts live with their owning module

**Status:** Accepted

**Decision.** No shared `prompts/` directory. Each agent keeps its prompts inside its own package —
`app/parsing/prompts/`, `app/review/prompts/`, `app/agent/prompts/` — as versioned files, not inline
string literals. `app/core/llm.py` provides the client and the structured-output plumbing; it holds
no prompt text.

**Reasoning.** A shared prompts directory is a shared-ownership directory, and shared ownership
across four parallel agents means merge conflicts and drift. Prompts belong to the module whose
behaviour they define. Keeping them in files rather than inline makes them reviewable and diffable —
a prompt change is a behaviour change and should read like one in the history.

---

## ADR-020 — No Alembic in v1; per-agent models, tables created at startup

**Status:** Accepted

**Context.** Three backend agents each define tables (B1: IR, versions; B2: source_store, cache;
B3: jobs, change sets, metrics). Alembic's linear revision chain conflicts badly under parallel
authorship — every agent generating a migration against the same head produces a merge every time.

**Decision.** No migrations for v1. Each agent declares SQLModel models in its own package with a
namespaced table prefix (`ir_`, `src_`, `agent_`); `create_all()` runs at startup; a documented
`make db-reset` drops and recreates.

**Reasoning.** Greenfield, no production data, no upgrade path to preserve. The cost of Alembic here
is pure coordination overhead paid daily for a benefit we do not yet need.

**Revisit:** the moment there is data anyone would be upset to lose.

---

## ADR-021 — Optimistic locking on IR versions

**Status:** Accepted

**Context.** Two commands submitted before the first is approved would silently lose one of them —
the second commits against a stale base and overwrites.

**Decision.** Every command and every change-set approval carries `base_version`. The commit fails
with a conflict if head has moved, and the UI re-plans against the new head rather than merging.

**Reasoning.** Silent lost updates in a document editor are exactly the class of invisible failure
HR-3 exists to prevent — the user would see a successful approval and a document missing their
earlier edit. Refusing and re-planning is honest; merging IR fragments automatically is not something
we can do safely without understanding both edits.

---

## ADR-022 — Uploads on a local volume; jobs have first-class status

**Status:** Accepted

**Decision.** Uploaded PDFs are written to a mounted volume at `/data/uploads/{doc_id}.pdf` with the
path recorded in Postgres. No object storage. Background jobs (`arq`) have a `jobs` row carrying
`status`, `error`, `progress_current`, `progress_total`, exposed over the API and rendered in the UI.

**Reasoning.** Object storage buys nothing in a single-node compose deployment. The jobs table
matters more than it looks: **a crashed background job is a failure, and HR-3 requires it to be
visible.** Without a status model, a worker that dies mid-review leaves the UI streaming nothing
forever, which a user reads as "no findings" — the same false-negative failure mode as ADR-010.

---

## ADR-023 — Single-user, no authentication, stated as scope

**Status:** Accepted

**Decision.** No auth, no accounts, no multi-tenancy. A document is identified by its `doc_id` and
anyone with the id can access it.

**Reasoning.** Stated rather than discovered. The evaluation is about parsing, grounding, and edit
safety; auth would consume build time and demonstrate nothing about any of them. Named here so it
reads as a decision, not an oversight — and so nobody deploys this publicly assuming otherwise.

---

## ADR-024 — All thresholds in one config module

**Status:** Accepted

**Decision.** Every magic number lives in `app/core/config.py` as a named, documented setting.
Starting values, to be tuned against the golden set — **these are hypotheses, not constants:**

| Threshold | Value | Meaning |
|---|---|---|
| `ARBITER_ACCEPT` | 0.85 | agreement score to accept an external record (ADR-001) |
| `REPAIR_TRIGGER` | 0.75 | parse_confidence below which the repair tier runs (ADR-003) |
| `REATTACH_ACCEPT` | 0.72 | cosine similarity to reattach an anchor silently (ADR-013) |
| `REATTACH_FLAG_FLOOR` | 0.55 | below this, no reattachment is proposed at all — user decides |
| `RERANK_KEEP` | 10 | candidates per claim surviving the embedding prefilter |
| `VERIFY_KEEP` | 3 | candidates per claim sent to the verifier |
| `CITABILITY_MIN` | 0.3 | below this a claim is not reviewed (and the count is displayed) |
| `STYLE_AMBIGUOUS_DELTA` | 0.05 | top-two gap below which style detection returns ambiguous |
| `DOC_TOKEN_BUDGET` | 2_000_000 | per-document LLM ceiling; exceeding it raises and surfaces |

**Reasoning.** Thresholds scattered as literals cannot be tuned, cannot be tested at their
boundaries, and cannot be reported. T1 needs to sweep several of these against the golden set;
that is only possible if they have names.

---

## ADR-025 — A matching DOI is identity, not similarity

**Status:** Accepted (amends ADR-001)

**Context.** ADR-001 and `goal.md` §7 fix the arbiter's agreement score at
`0.6·title_sim + 0.2·year_match + 0.2·first_author_sim`, accepted at ≥ 0.85. Applied literally to a
reference whose parse yielded a DOI but no title — common, because GROBID often recovers an `idno`
from a mangled entry it could not otherwise segment — the score has a ceiling of 0.4. Such a
reference could never resolve, no matter how certainly we knew which work it was.

**Decision.** When our parse and the external record both carry a DOI and the two DOIs are equal
(case-insensitively, prefix-stripped), the agreement score is **1.0**, labelled `doi_identity` in
the breakdown. Every other case uses the specified formula exactly as written, including when both
sides carry DOIs that differ.

**Reasoning.** The formula estimates whether an external record *is* the work our reference refers
to. A DOI is a unique identifier for exactly that: two records sharing one are the same work by
definition, and running a fuzzy title comparison over that fact answers a question already answered
exactly. This is not a loosening — a DOI mismatch still falls through to the formula, and the
0.85 threshold is untouched. The label is exposed so the audit view can show *why* a reference
resolved, and a DOI-identity match is visibly a different kind of evidence from a fuzzy one.

**Consequences.** A wrong DOI in the source PDF resolves confidently to the wrong paper. That is
true of any DOI-first design, including ADR-001's own cascade, and the raw string is retained so
the error is inspectable rather than hidden.

---

## ADR-026 — One IR span is one sentence

**Status:** Accepted (refines ADR-004)

**Context.** ADR-004 fixes the IR as sections → blocks → spans with text living only in spans, but
does not say how big a span is. GROBID will segment sentences on request (`segmentSentences=1`),
producing `<s>` elements inside each `<p>`.

**Decision.** A span is a sentence. A paragraph block holds one span per sentence; a `<p>` with no
`<s>` children falls back to a single span for the whole paragraph.

**Reasoning.** Two downstream requirements are stated in terms of sentences and become awkward at
paragraph granularity. Reattachment (ADR-013) scores an anchor's `context_fingerprint` against "the
new sentences" — with paragraph spans, every anchor in a rewritten paragraph would score against
one large blob and reattach arbitrarily. Claims (CP-5) carry a `span_id`, and a claim that points
at a whole paragraph cannot be shown to a user as the thing being checked. Sentence spans also make
`offset_in_span` small and stable, so an edit to one sentence does not move every anchor in the
paragraph.

**Consequences.** More spans per document (195 for a 71-paragraph paper), so traversal is over a
larger list; all of it is linear and none of it is hot. Sentence segmentation quality becomes a
GROBID dependency — a bad split produces two spans where there should be one, which is visible and
recoverable rather than silent.

---

## ADR-027 — A repair-tier violation discards the whole entry, not just the offending field

**Status:** Accepted (sharpens ADR-003)

**Context.** ADR-003 says the post-check "discards any emitted field value that is not a substring
of the raw string; the entry is then marked unparsed rather than accepted". That admits two
readings: drop the bad field and keep the rest, or reject the entry entirely.

**Decision.** Any violation discards the offending value **and** marks the whole entry unparsed.
The entry goes to `quarantined` with its raw string retained verbatim, and the violations are kept
for display.

**Reasoning.** The stricter reading. A model that invented one field has demonstrated it will
invent; there is no principled basis for trusting the remaining fields of the same output, which
were produced by the same process in the same call. Keeping the "good" fields would mean shipping a
record we have positive evidence to distrust, and the failure would be invisible because the
remaining fields *do* pass the check. Quarantine is cheap — the reference is still shown to the
user with its real text — and a false quarantine costs far less than a fabricated author.

**Consequences.** A single over-eager expansion (`Proc.` → `Proceedings`) costs the whole entry.
Observed in testing and accepted: those entries reach the arbiter as unresolved and can still be
recovered from the raw string by an external match.

---

## ADR-028 — Provider disagreement is data, not corruption

**Status:** Accepted (amends HR-1's scope; supersedes nothing)

**Context.** ADR-025 makes a matching DOI identity, so `mint_source_id` deliberately maps every
provider that resolves the same DOI onto one `source_id` — the second `put()` is meant to *enrich*
the first, which is why the store is keyed `(source_id, version)`. It never worked. `_merge_append_only`
treated any differing value as an overwrite attempt and raised `AppendOnlyViolation`, which nothing
catches, so one reference killed the whole ingest.

Measured against 61 cached provider responses from a real 40-reference paper: 87 distinct
`source_id`s, 4 described by more than one provider, and **4 of 4 would raise — a 100% collision
rate. Not one row in the store had ever reached version 2.** The enrichment path had never once
executed. Normalizing the obvious dialect differences (Crossref's `journal-article`, the per-provider
`custom` bag) was simulated and fixes **0 of the 4**: what remains is providers genuinely disagreeing
— S2 calls an ICASSP paper `paper-conference` where OpenAlex calls it `article-journal`, S2 has the
fuller title, they differ on publication year by one, and each returns its own canonical URL.

**Decision.** Differing values are no longer uniformly fatal. Three classes, handled differently:

* **Identity** — `DOI`. A change here is still `AppendOnlyViolation`, still fatal, still
  uncatchable. This is the part of HR-1 that makes fabrication structurally impossible and it is
  untouched. An abstract already stored from a real response is still never *replaced* — see the
  amendment below, which keeps that guarantee and drops only the raise.
* **Provider-specific and mutable fields** — `citation_count`, `crossref_score`, `is_open_access`,
  `raw_author_names`. Namespaced under `custom.providers.<name>` and never compared. A citation
  count is a measurement that changes between two reads of the same record; storing it as a value
  that may never change was a category error.
* **Descriptive fields** — title, author, publisher, container-title, type, issued, URL. A
  difference **appends a version** recording both readings with the provenance of each. The
  first-stored value stays canonical; the alternative is preserved beside it.

**Reasoning.** The store was being used as a reconciliation point, and it is not one — `Arbiter`
is, scoring `0.6·title_sim + 0.2·year_match + 0.2·first_author_sim` against an accept threshold.
Two mechanisms were deciding the same question and the store's veto fired first, before the
arbiter's judgment could mean anything. A second provider describing a paper differently is
additional information, not an attempt to corrupt the first.

The first-stored value stays canonical deliberately: a finding's quote may already have been
substring-checked against it, and silently promoting a "better" title would move ground the
verifier already stood on. Choosing between readings stays with the arbiter, which has the
agreement score to do it with; the store's job is to lose nothing.

**Amendment — a rival abstract is a disagreement too.** The original decision left a differing
abstract in the fatal class, and that class had one member ADR-006 puts there by design. The chain
is S2 → OpenAlex inverted → S2 TLDR: when S2 has no licensed abstract it stores its TLDR and the
chain *carries on* to OpenAlex, which ranks higher. So the normal path wrote a second, better
abstract onto a `source_id` that already held one, and raised — uncatchably, in the `review` state,
for every reference where S2 was unlicensed and OpenAlex had an inverted index. The store was
vetoing the chain it was built to serve, which is the same mistake ADR-028 was written to correct,
one field later.

An abstract now behaves like a descriptive field: **the stored one stays canonical and the rival is
recorded beside it** with its own source and provenance. The guarantee the fatal class existed for
is intact — a stored abstract is still never replaced, so a quote already substring-checked against
it still holds. What changed is only that the alternative is kept instead of refused. `AbstractResolver`
stays the thing that ranks, and it ranks live on every `resolve()`, so the verifier reads OpenAlex's
fuller abstract regardless of which one the store calls canonical. The store does not get a second
vote; that was the whole argument above.

Note the blind spot that let this reach production: every test of the chain stubbed *both*
providers, so no test had ever put two real abstracts into one store.

**Consequences.** Records now reach version 2+, which is what the composite key was always for.
A reference where providers disagree no longer fails the paper. The disagreements are retained and
are the obvious raw material for showing a reviewer *why* two sources describe one work
differently, which we do not do yet.

`prefilter` and `rerank` read `record.abstract` directly, so for a TLDR-first record they see the
one-liner rather than OpenAlex's fuller text — a ranking-quality cost, not an honesty one, and the
same trade-off ADR-005 already accepts. Routing them through the resolver is the fix if it matters.

`source_store` gains a `disagreements` column. A fresh database gets it from `create_all()`; an
existing one took a single additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, applied by hand
rather than by a `db-reset` because dropping the table would have orphaned the `source_id`s that
already-parsed documents point at. This does not reopen ADR-020: the exception is *additive DDL
with a default*, which cannot lose data and cannot fail against existing rows. A column being
dropped, renamed or retyped is still the Alembic conversation ADR-020 defers, and still needs its
own decision.

---

## ADR-029 — Rule 4 judges the change, not the document it found

**Status:** Accepted (sharpens the kernel rules in Appendix A)

**Context.** "IR schema violation" was implemented as a whole-document scan of the *after*
document, and one of its checks was that every `CitationAnchor` carries at least one
`source_id`. But B1 creates every parsed anchor with `source_ids=[]` and fills it in only when
the arbiter resolves that reference. Two CP-2 tiers never resolve by design: `orphan_marker`
(an in-text marker with no matching bibliography entry) and `parsed_unresolved`. The anchor is
still created — deliberately, so the marker survives export and the user can see where it sits.

The consequence was total. A document with a single orphan marker could not be edited *at all*:
every operation, anywhere in the paper, came back REJECT with a list of anchors the operation
had never touched, after burning both planner retries on feedback no plan could act on. Observed
on a real 40-reference paper (34 resolved, 6 unresolved, 4 orphan markers) — 7 sourceless
anchors, and every command refused.

**Decision.** Rule 4's sourceless-anchor check compares against the before document. It rejects
exactly two things: an anchor the change **added** with no sources, and an anchor whose sources
the change **emptied**. An anchor that was already sourceless and still is passes.

**Reasoning.** The kernel's authority is over what an edit does. A pre-existing parse state is
not something an edit can be guilty of, and refusing an edit for it is both unactionable — no
rewording of the command can fix it, so the retry budget is spent on nothing — and misleading:
HR-3 asks that a refusal say what is wrong, and "anchor `anc_x` carries no source_ids" invited
the reader to think the edit had broken a citation.

Nothing is loosened that another rule was holding. A fabricated `source_id` is rule 1, a
shrinking citation multiset is rule 2 (HR-5), an unsupported new claim is rule 3, and both
newly-empty cases above are still rule 4. What is given up is the kernel acting as a validator
for B1's output, which was never its job: a parse that loses references reports that in CP-2's
count strip, where it can be acted on.

**Consequences.** Papers with unresolved references — that is, most real papers — are editable.
An orphan-marker anchor now travels through detach → transform → reattach like any other; with
no sources it renders in the edit console as its anchor id rather than a citation label, which
is honest but plain, and is the obvious thing to improve when orphan markers get first-class
treatment in the UI.

---

## ADR-030 — An ambiguous style is disclosed, not blocking

**Status:** Accepted (amends the "user must pick" clause of goal.md CP-3)

**Context.** Style detection scores the paper's raw reference strings against each candidate
`.csl` and, when the top two land within `STYLE_AMBIGUOUS_DELTA`, returns `ambiguous` with
`style_id=None` rather than guessing. The ingest pipeline then wrote that `None` straight onto
`document.metadata.style_id`.

Export re-renders every citation and the whole bibliography through citeproc (HR-4), so it needs
a style. With `style_id` null it raised `ExportFailure`, and a paper whose style could not be
separated from a near neighbour was **permanently unexportable** — the core deliverable of the
product, unreachable, for a formatting distinction the user very likely does not care about.

The escape hatch was the picker on the parse screen. Two problems. It is reachable only through
the parse report, which is held in-process and dies on an API restart, taking the only route to
a style with it. And it asked the user to arbitrate a question they had no way to answer: the API
sent the same static six-style list for every document, never the two candidates that actually
tied or their scores.

Measured on a real 38-anchor paper (`doc-a971392fdabc`): author-date markers throughout, so
marker-family narrowing had already eliminated the four numeric styles. The tie was APA against
Chicago author-date at 0.474. Both render in-text citations identically; the entire difference is
punctuation and field order in the reference list.

**Decision.** Detection stays honest and unchanged — a tie still returns `style_id=None`,
`ambiguous=True`, with both candidates scored. What changes is the *policy* applied to that
measurement, which now lives at the ingest boundary rather than inside the detector:

1. The pipeline persists the **closest candidate** as `metadata.style_id` even on a tie, and
   leaves `style_ambiguous=True` next to it. The document is always self-sufficient: export
   works from Postgres alone, with no dependency on a live parse report.
2. `ExportManifest` carries `style_uncertain`, and the export screen states which style it is
   rendering in and that the call was close, with the alternatives one click away.
3. A user's explicit choice clears `style_ambiguous` — it is an answer, not a measurement, so no
   confidence score is reported beside it.

**Why not guess silently.** Considered and rejected. A reference list quietly reformatted into a
style the author did not write in is precisely the "quietly rewrites their paper into something
they no longer recognize" failure the product exists to avoid. The guess is fine; the guess
*undisclosed* is not. HR-3 is satisfied by saying what we did and how sure we were, not by
refusing to act.

**Consequences.** Export is never blocked by a style question. goal.md CP-3's "Top-two within
0.05 → returns `ambiguous`, user must pick" is now "→ returns `ambiguous`, closest match is used
and disclosed, user may override" — the detector's contract is unchanged, so the CP-3 bullets
about scoring, the shortlist and the exposed numeric score all still hold as written. **goal.md
is owner-edited, and that one clause needs amending to match.** Logged in memory.md under
Interface Requests / Blockers.
