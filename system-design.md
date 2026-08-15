# System Design — Paper Review & Agentic Editing

**Status:** decisions locked. This supersedes `design-options.md` (kept for the reasoning trail).
**Date:** 15 Aug 2026

## Locked decisions

| Axis | Choice |
|---|---|
| PDF → structure + citations | **D1-c**, layered cascade with an external arbiter — *simplified to two parse tiers* |
| Document representation | **D2-c**, purpose-built IR; LaTeX/Pandoc as a render target only |
| Grounding | **D3-b**, claim-first semantic retrieval with quote-backed entailment |
| Agent control | **D4-c**, planner → typed operations → invariant kernel → diff → approve |
| Editing escape hatch | `FreeformEdit` included, gated behind an explicit `no_typed_op_applies` justification |
| Review execution | Background job, findings streamed as they verify |
| GROBID | Docker sidecar |
| Export fidelity | Text + citations exact; figures/tables/equations become visible placeholders |
| Stack | FastAPI backend, Next.js frontend |

**API keys are required, not optional.** `SEMANTIC_SCHOLAR_API_KEY` and `OPENALEX_API_KEY` must both
be present or **the application refuses to start**. There is no anonymous mode and no degraded
banner. Rationale: under anonymous limits searches do not error, they return thin or empty results,
which the review pipeline would faithfully report as *"no missing work found"* — a false negative
dressed as a clean bill of health, and indistinguishable downstream from a genuine empty result.
The only safe design is to make the misconfiguration impossible to run.

---

## 1. Stack and topology

```
┌────────────────────┐        ┌──────────────────────────────────────┐
│  Next.js frontend  │        │  FastAPI backend                     │
│  - upload          │◄──SSE──┤  - REST + Server-Sent Events         │
│  - parse inspector │        │  - IR store (versioned)              │
│  - review feed     │──REST─►│  - planner / kernel / executor       │
│  - diff + approve  │        │  - provider adapters + rate limiter  │
│  - citation.js     │        │  - Pandoc (export)                   │
│    (live preview)  │        └───────┬───────────────┬──────────────┘
└────────────────────┘                │               │
                                      ▼               ▼
                          ┌───────────────────┐  ┌──────────────────┐
                          │ GROBID (sidecar)  │  │ Postgres + Redis │
                          │ :8070             │  │ IR, cache, jobs  │
                          └───────────────────┘  └──────────────────┘
                                      │
                                      ▼
                      Semantic Scholar  ·  OpenAlex  ·  Crossref
```

**Why this split works for us:** CSL rendering happens in two places and the stack gives us the
right library in each. `citation.js` in Next.js renders citations live in the editor as the user
works; **Pandoc** on the backend renders the authoritative bibliography at export. Both read the
**same `.csl` files** from a shared volume, so preview and export cannot drift. Nothing formats a
citation with a string template, anywhere.

Key libraries: `httpx` + `aiolimiter` (provider calls), `lxml` (TEI), `arq` on Redis (jobs),
SQLModel/Postgres (IR + cache), `pypandoc` (export), Server-Sent Events (streaming findings).

---

## 2. Citation parsing pipeline

### 2.1 Stages

```
PDF
 │
 ├─[S1] GROBID processFulltextDocument
 │        consolidateHeader=1, consolidateCitations=1,
 │        teiCoordinates=[ref,biblStruct,head,p]
 │      → TEI XML
 │
 ├─[S2] TEI → Document IR
 │        sections/blocks/spans; <ref type="bibr" target="#bN">
 │        becomes a citation_anchor node; PDF coords retained
 │
 ├─[S3] biblStruct → CSL-JSON (provisional)
 │        per-entry parse_confidence from field completeness + GROBID signals
 │
 ├─[S4] Repair tier (only entries below threshold)
 │        constrained LLM: SEGMENT AND LABEL the literal characters given.
 │        Hard rule — may not emit a token absent from the input string.
 │        Post-check: every emitted field value must be a substring of the raw
 │        entry (whitespace/punct normalised) → else discard, mark unparsed.
 │
 ├─[S5] ARBITER — external reconciliation (the load-bearing step)
 │        a. DOI present?          → Crossref /works/{doi}
 │        b. else title present?   → S2 /paper/search/match
 │        c. else / S2 miss        → OpenAlex /works?search=
 │        Accept a match only if agreement_score ≥ 0.85, where
 │          agreement = 0.6·title_sim + 0.2·year_match + 0.2·first_author_sim
 │        On accept: external record REPLACES our parse as canonical CSL-JSON,
 │                   we keep our raw string + our parse for the audit view.
 │
 ├─[S6] Style detection by round-trip scoring
 │
 └─[S7] Commit: IR v0 + source_store + quarantine[]
```

### 2.2 Why the arbiter is the centre of gravity

"Did we parse this reference correctly?" is unanswerable without the ground truth we're trying to
produce. The arbiter replaces it with "does this resolve to a real record that agrees with what we
extracted?", which is answerable. Three consequences:

- A mediocre parse that still matched the right paper **self-heals** — we keep clean external
  metadata, not our noisy version.
- Every reconciled reference gains a **real, linkable URL**, which is what makes a finding clickable
  and what proves a source was not fabricated.
- A reference that resolves nowhere is **definitionally suspect**, giving `quarantine[]` a principled
  entry condition rather than a hand-tuned confidence cutoff.

Cost control: the arbiter runs as a background job at ingest, batched. Once IDs are known, S2
`/paper/batch` hydrates up to 500 records in a single call. Every response is cached by
`(provider, endpoint, normalized_query_hash)`.

### 2.3 Confidence tiers, surfaced in the UI

| Tier | Condition | Shown as |
|---|---|---|
| `resolved` | arbiter matched ≥ 0.85 | green, with link to the real record |
| `parsed_unresolved` | parsed cleanly, no external match | amber — "we parsed this but couldn't find it" |
| `low_confidence` | parsed via repair tier, no match | amber, fields marked individually |
| `quarantined` | segmentation succeeded, parse failed | red, **raw string shown verbatim** |
| `orphan_marker` | in-text marker with no matching entry | red, located in the document |

`quarantined` and `orphan_marker` are never dropped and never hidden. The parse-inspector screen
opens with a count of each.

### 2.4 Style detection by round-trip scoring

Deterministic and explainable, in place of asking a model:

1. Classify marker family from the in-text markers — `[12]` / superscript → numeric,
   `(Author, 2020)` → author-date. Narrows the candidate set.
2. For each candidate `.csl` in the shortlist (APA 7, IEEE, ACM, Nature, Chicago author-date,
   Vancouver), render our reconciled CSL-JSON through **Pandoc/citeproc**.
3. Compare each rendered entry against the **raw reference string we actually extracted** using
   normalised Levenshtein; average across the bibliography.
4. Pick the argmin. **Show the score.** If the top two are within 0.05, declare it ambiguous and ask
   the user to pick.

This doubles as a regression test for the whole pipeline: if parsing degrades, round-trip similarity
drops before anything else does.

---

## 3. The Document IR

Text lives **only** in spans. Citations are **nodes with stable IDs**, never characters in a string.

```jsonc
{
  "doc_id": "…", "version": 7,
  "metadata": { "title": "…", "style_id": "ieee", "style_confidence": 0.91 },
  "sections": [{
    "id": "sec_1", "level": 1, "title": "Introduction", "order": 0,
    "blocks": [{
      "id": "blk_12", "type": "paragraph", "order": 3,
      "spans": [{
        "id": "spn_44",
        "text": "Transformer models outperform recurrent architectures on long sequences.",
        "citation_anchors": [{
          "anchor_id": "anc_9",
          "source_ids": ["src_a1b2"],          // FK into source_store — required
          "offset_in_span": 71,
          "original_marker_text": "[12]",
          "provenance": "parsed",              // parsed | agent_added
          "confidence": 0.97,
          "context_fingerprint": "…"           // embedding of the host sentence
        }]
      }]
    }]
  }],
  "source_store": {                            // APPEND-ONLY. API adapters write. Nothing else.
    "src_a1b2": {
      "csl": { /* CSL-JSON */ },
      "provenance": { "provider": "semantic_scholar", "endpoint": "/paper/search/match",
                      "retrieved_at": "…", "external_url": "https://…" },
      "abstract": "…", "abstract_source": "s2" // s2 | openalex_inverted | tldr | unavailable
    }
  },
  "quarantine": [{ "raw": "[7] Smth, J. Neral netwrks…", "reason": "parse_failed", "page": 9 }]
}
```

**The single most important property:** `source_ids` is a foreign key into a store only an API
adapter can write to. An LLM cannot mint a source. It can only *reference* something a real HTTP
response already put there. Fabrication is excluded by the type system, not by a system prompt.

Versioning is copy-on-write per approved change set, so every edit is revertible and the diff view
is a structural comparison, not a text diff.

---

## 4. Peer review

### 4.1 Pipeline

```
IR ──► CLAIM EXTRACTION (LLM, structured output)
         atomic, citable assertions; each carries span_id + anchor_ids
         each scored for "citability" (bald empirical assertion = high,
         "In this section we describe…" = zero)
         │
         ├──► ordered by citability, streamed highest-first
         ▼
    per claim, in parallel, rate-limit-governed:
      ┌──────────────────────────────────────────────────────────┐
      │ CANDIDATE GENERATION (multi-strategy)                     │
      │  1. S2 /snippet/search       → passage-level evidence      │
      │  2. S2 Recommendations       → seeded with the paper's OWN │
      │       cited works (SPECTER2 space) = the real semantic     │
      │       signal for "what did this neighbourhood contain      │
      │       that they missed"                                    │
      │  3. OpenAlex search + one-hop cited_by / references        │
      │       expansion from the existing bibliography             │
      └──────────────────────────────────────────────────────────┘
         │  fuse (reciprocal-rank), dedupe by DOI/S2 id,
         │  SUBTRACT everything already cited
         ▼
      RERANK against THIS CLAIM (not the topic)
         ▼
      VERIFY ── fetch abstract: S2 → OpenAlex inverted index → TLDR → unavailable
         label ∈ { supports, partially_supports, does_not_address,
                   contradicts, unverifiable_no_abstract }
         REQUIRED: verbatim quote from the fetched abstract
         MECHANICAL CHECK: quote must be a substring of the abstract → else discard finding
         ▼
      emit Finding ──SSE──► UI
```

### 4.2 One verifier, two callers

The same verify stage serves both review tasks. Pointed at **candidates** it answers *"is this
missing work you should cite?"*. Pointed at **existing anchors** it answers *"does the source you
cited actually support this claim?"*. Identical code path, identical evidence format.

### 4.3 Honesty surfaces

- No abstract retrievable → `unverifiable_no_abstract`. This is a **displayed outcome**, not a
  silent skip.
- Zero candidates after subtraction → "no missing work found for this claim" is a finding too.
- The quote-substring check is mechanical: a quote not present in the fetched abstract kills the
  finding. A model cannot talk its way past it.
- Progress shows `claims verified / total claims` throughout, so partial state is never mistaken
  for complete coverage.

---

## 5. The agent

### 5.1 Control flow

```
"add more citations to the introduction"
        │
        ▼
  PLANNER (LLM, structured output only — cannot emit prose or text edits)
        │  EditPlan = [Operation]
        ▼
  RESOLVER (per operation — may call S2/OpenAlex; may call LLM to write text)
        │  ProposedChange { op, target ids, new IR fragment, new source_ids, rationale }
        ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ INVARIANT KERNEL — pure code, no LLM                        │
  │  REJECT if:                                                  │
  │   · any source_id ∉ source_store                             │
  │   · citation multiset shrinks without an approved removal op │
  │   · a newly asserted claim has no anchor with a verified src │
  │   · IR schema violation                                      │
  │   · Pandoc refuses to render the result                      │
  │  FLAG (show, warn, let the user decide) if:                  │
  │   · an anchor reattached below similarity threshold          │
  │   · an anchor found no home after a transform                │
  │   · a source's verification label is weaker than `supports`  │
  └─────────────────────────────────────────────────────────────┘
        │  REJECT → returned to planner with the reason (max 2 retries, then surface)
        ▼
  DIFF (citation-aware, rendered) ──► USER APPROVES ──► commit IR v+1
```

**REJECT vs FLAG is a deliberate line.** REJECT = structurally invalid, thrown away, planner told
why. FLAG = valid but uncertain, shown to the user with the warning attached. Blurring these is how
this class of system either silently discards good edits or waves through bad ones.

### 5.2 Operation vocabulary

| Operation | Semantics |
|---|---|
| `AddCitations(target, count, criteria)` | Find claims lacking support, retrieve, verify, insert anchors |
| `FindSupport(claim_ids)` | Retrieve + verify only; propose anchors, assert nothing new |
| `Shorten(target, ratio)` | Detach → compress → reattach (see 5.3) |
| `RewriteSection(target, instruction)` | Same detach/reattach discipline, no length target |
| `ReplaceCitation(anchor, source)` | Swap a source, keeping the anchor and its position |
| `MoveText(from, to)` | Relocate spans with their anchors attached |
| `FreeformEdit(target, instruction)` | Escape hatch — same kernel, generic invariants only |

`FreeformEdit` requires the planner to emit `no_typed_op_applies` with a one-line justification, and
we log its firing rate. **Above ~20% of commands, the typed vocabulary is wrong and we fix the
vocabulary rather than lean on the hatch.**

### 5.3 Detach–transform–reattach — how citations survive

Shortening never regenerates text with citations inline. That is the whole trick.

```
1. DETACH   pull anchors out of the target; record each one's
            context_fingerprint (embedding of its host sentence)
2. TRANSFORM  compress/rewrite the TEXT ONLY — the model never sees
            or emits citation markers
3. REATTACH  for each anchor, score its fingerprint against every new
            sentence; attach at argmax if ≥ threshold
4. SURFACE  any anchor below threshold is NOT dropped — it is raised to
            the user: "this citation no longer has an obvious home:
            keep here / move to … / remove"
```

Step 4 is the requirement. "Citations stay attached to the right context when text moves or shrinks"
is met by making reattachment failure a **visible outcome** rather than a silent deletion.

---

## 6. Rate limits, caching, jobs

- **Keys enforced at startup.** Provider constructors raise `MissingAPIKeyError`; nothing catches it
  to degrade. A rate-limit exhaustion also raises — it never returns an empty result list, because
  "throttled" and "nothing found" must never be indistinguishable to the caller.
- **Token-bucket limiter per provider.** S2 ~1 rps; OpenAlex 100 rps but credit-metered (1 singleton
  / 10 list / 1000 vector — the vector endpoint stays budget-gated and off by default).
- **Every response cached** in Postgres keyed by `(provider, endpoint, normalized_query_hash)` with a
  TTL. Re-review of the same paper is near-free; this is also what makes the demo reproducible.
- **`arq` workers** for ingest-arbitration and review. The API returns a `job_id` immediately;
  findings stream over SSE as each verifies.
- All calls carry `mailto` for the OpenAlex polite pool.
- Provider adapters are the **only** code allowed to write to `source_store`.

---

## 7. Testing

- **Golden set:** 5–8 real arXiv PDFs spanning IEEE numeric, APA author-date, ACM, and Nature
  superscript styles, with hand-checked expected sections, reference count, and 10 spot-checked
  parsed entries each.
- **Parsing metrics:** reference recall, field-level precision, arbiter resolution rate,
  orphan-marker count, and **mean round-trip style similarity** (a single number that regresses
  before anything else does).
- **Kernel tests:** pure functions, no model. Fixture IR + hand-built malicious ProposedChanges —
  fake `source_id`, dropped anchor, unsupported claim — each asserted to REJECT.
- **Agent tests:** recorded API fixtures (VCR-style) so review and edit flows are deterministic in CI.

---

## 8. Build order

Vertical slice first — a working round trip on day 3, not day 9.

| # | Milestone | Proves |
|---|---|---|
| 1 | Upload → GROBID → TEI → IR → **LaTeX export** | The IR round-trips. Everything else depends on this. |
| 2 | Arbiter + confidence tiers + parse-inspector UI | Requirement 1, including the honest failure surfaces |
| 3 | Style detection by round-trip scoring | CSL is real, not a template |
| 4 | Claim extraction + verify stage, streamed | Requirement 2 |
| 5 | Candidate generation (all three strategies) + fusion | Requirement 2 at full strength |
| 6 | Planner + kernel + 3 ops (`AddCitations`, `Shorten`, `FindSupport`) + diff/approve | Requirement 3 |
| 7 | Remaining ops incl. `FreeformEdit` | Coverage |
| 8 | Golden-set harness + README/system-design writeup | The graded artifact |

**Known scope cut, stated rather than discovered:** figures, tables, and equations do not survive the
LaTeX round trip; they render as visible placeholders carrying their captions. Text, structure,
citations, and the bibliography survive exactly.
