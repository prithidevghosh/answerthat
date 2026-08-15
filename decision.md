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

**Status:** Accepted

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
