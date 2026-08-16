# goal.md — The Definition of Done

**Every coding agent on this project MUST read this file first and re-read it before claiming any
checkpoint complete.** This file is the contract. `decision.md` explains *why*; `memory.md` records
*what we learned*. This file defines *what "finished" means*.

Nothing here may be changed by a coding agent. If a checkpoint is wrong or impossible, write the
problem into `memory.md` under **Interface Requests / Blockers** and stop — do not silently
reinterpret a checkpoint.

---

## 1. What we are building

A web app where a researcher uploads a paper as a PDF, receives a peer review grounded in real
academic search (Semantic Scholar + OpenAlex), and can then edit the paper by natural-language
instruction — while every citation in the paper survives intact.

The first screen is the product: an upload. Not a landing page.

---

## 2. The five hard rules

These are non-negotiable and override every other consideration including deadline, elegance, and
demo polish. A checkpoint that violates one of these is **not complete**, regardless of whether it
"works".

**HR-1 — No fabricated sources, structurally.**
An LLM may never mint a citation. `source_id` is a foreign key into an append-only `source_store`
that only a provider adapter (`app/providers/*`) may write to, and only from a real HTTP response.
Any code path where a model's output becomes a citation without passing through a provider adapter
is a defect of the highest severity.

**HR-2 — Fail fast on credentials whose absence degrades silently.**
`OPENALEX_API_KEY`, `OPENALEX_MAILTO`, and `OPENAI_API_KEY` are **required**. The application must
raise on startup if any is absent or empty. For these there is no anonymous mode, no degraded
banner, no default route: a provider that cannot authenticate must raise `MissingAPIKeyError`, never
return empty results that read like "nothing found".

The rule is about the *failure mode*, not the credential. `SEMANTIC_SCHOLAR_API_KEY` is **optional**
because S2 throttles with an HTTP 429 that we already raise on, so an unauthenticated review cannot
under-report silently. Adding a second exception requires proving the same property.
*Rationale in `decision.md` ADR-010, ADR-010a, ADR-015.*

**HR-3 — Failures are surfaced, never swallowed.**
Unparseable references, unresolved references, orphaned in-text markers, missing abstracts,
zero-result searches, low-confidence citation reattachment — each has a defined visible state in the
UI. `except: pass`, empty-list-on-error, and "best effort" fallbacks are prohibited. If we don't
know, the system says it doesn't know.

**HR-4 — CSL is the only citation model.**
Every citation, parsed or retrieved, is CSL-JSON. Every rendered citation and bibliography goes
through a citeproc implementation reading a real `.csl` file — Pandoc on the backend, `citation.js`
in the frontend, sharing `packages/csl-styles/`. Zero hand-written citation formatting. Zero regex
citation templates. A single f-string that builds a citation string is a rule violation.

**HR-5 — Citations survive every edit.**
No edit operation may reduce the multiset of `source_id`s reachable from the document unless the
user explicitly approved a removal. Text transforms use **detach → transform → reattach**; the model
never sees or emits citation markers during a rewrite. An anchor that cannot be reattached above
threshold is raised to the user, never dropped.

---

## 3. Repository layout and ownership

Monorepo. **An agent may only create or modify files under the paths it owns.** Touching another
agent's path is a protocol violation — file an Interface Request in `memory.md` instead.

```
/
├── goal.md decision.md memory.md README.md
├── docker-compose.yml .env.example
├── apps/
│   └── web/                          ◄── OWNER: F1 (frontend)
├── services/
│   └── api/
│       ├── app/
│       │   ├── core/                 ◄── FROZEN. Materialized by B1 from Appendix A.
│       │   │   ├── contracts.py          Changes require an ADR in decision.md.
│       │   │   ├── config.py            all keys + all thresholds (ADR-024)
│       │   │   ├── llm.py               OpenAI client, structured output,
│       │   │   │                        record/replay, embeddings (ADR-015/016/018)
│       │   │   ├── errors.py
│       │   │   └── db.py
│       │   ├── ir/                   ◄── OWNER: B1   (tables: ir_*)
│       │   ├── parsing/              ◄── OWNER: B1   (+ parsing/prompts/)
│       │   ├── export/               ◄── OWNER: B1
│       │   ├── providers/            ◄── OWNER: B2   (tables: src_*)
│       │   ├── review/               ◄── OWNER: B2   (+ review/prompts/)
│       │   ├── agent/                ◄── OWNER: B3   (+ agent/prompts/, tables: agent_*)
│       │   └── api/                  ◄── OWNER: B3
│       └── tests/
│           ├── unit/<owner>/         ◄── each agent owns its own unit tests
│           ├── golden/               ◄── OWNER: T1
│           ├── kernel/               ◄── OWNER: T1
│           └── e2e/                  ◄── OWNER: T1
└── packages/
    └── csl-styles/                   ◄── OWNER: B1 (shared read-only for others)
```

**Bootstrap order:** B1 commits `app/core/` verbatim from **Appendix A** as its very first commit.
B2 and B3 may begin immediately against the Appendix A spec — it is authoritative, so their code
will compile against B1's commit when it lands.

---

## 4. Checkpoints

An agent marks a checkpoint complete only when **every** acceptance criterion is demonstrably true.
"Demonstrably" means a test, a command whose output you pasted into `memory.md`, or a screenshot.

### CP-1 — Skeleton round trip *(B1)*
- [ ] `docker compose up` starts api, web, grobid, postgres, redis
- [ ] Missing **either** of `OPENALEX_API_KEY`, `OPENAI_API_KEY` **aborts startup with a clear error** (HR-2)
- [ ] A missing `SEMANTIC_SCHOLAR_API_KEY` **starts, logs the unauthenticated regime, and still raises on a 429** (ADR-010a)
- [ ] `app/core/` matches Appendix A exactly
- [ ] `app/core/llm.py`: OpenAI client with per-role model routing (ADR-015), mandatory JSON-Schema structured output, `embed()` at 512 dims (ADR-016), and `LLM_MODE=record|replay|live` where **replay raises on a cache miss** (ADR-018)
- [ ] Every model ID and every threshold lives in `config.py` and **nowhere else** (ADR-015, ADR-024)
- [ ] Per-document token budget enforced; exceeding it raises and surfaces
- [ ] `anchor_fingerprints` side table exists; no vector is stored inline in the IR (ADR-017)
- [ ] Uploads written to `/data/uploads/{doc_id}.pdf`; `jobs` table with status/error/progress (ADR-022)
- [ ] PDF upload → GROBID → TEI → Document IR persisted with a version number
- [ ] IR → LaTeX export renders through Pandoc without error
- [ ] Round trip preserves: title, all section headings and order, paragraph count ±0, every in-text anchor

### CP-2 — Parsing, arbitration, honesty *(B1, with B2's adapters)*
- [ ] Every `biblStruct` becomes provisional CSL-JSON with a `parse_confidence`
- [ ] Repair tier runs only below threshold, and **discards any field value that is not a substring of the raw reference string**
- [ ] Arbiter resolves via Crossref DOI → S2 `/paper/search/match` → OpenAlex, accepting only at `agreement_score ≥ 0.85`
- [ ] On accept, the external record replaces our parse as canonical; our raw string and our parse are retained for audit
- [ ] All five confidence tiers implemented and populated: `resolved`, `parsed_unresolved`, `low_confidence`, `quarantined`, `orphan_marker`
- [ ] Zero references are dropped: `len(resolved) + len(parsed_unresolved) + len(low_confidence) + len(quarantined) == total detected`
- [ ] Orphan in-text markers are detected and located

### CP-3 — Style detection *(B1)*
- [ ] Marker-family classifier (numeric vs author-date) implemented
- [ ] Round-trip scoring: render our CSL-JSON through each shortlisted `.csl` via Pandoc, compare to the extracted raw strings by normalised Levenshtein
- [ ] Shortlist present in `packages/csl-styles/`: APA 7, IEEE, ACM, Nature, Chicago author-date, Vancouver
- [ ] Winning style + **numeric score** exposed via API
- [ ] Top-two within 0.05 → returns `ambiguous`, user must pick

### CP-4 — Providers *(B2)*
- [ ] Adapters for Semantic Scholar, OpenAlex, Crossref, each behind the `Provider` protocol in Appendix A
- [ ] **Both keys required at import/startup — `MissingAPIKeyError` raised, no fallback** (HR-2)
- [ ] Token-bucket limiter per provider (S2 ~1 rps; OpenAlex credit-aware)
- [ ] All calls send `mailto` for the OpenAlex polite pool
- [ ] Response cache in Postgres keyed by `(provider, endpoint, normalized_query_hash)` with TTL
- [ ] OpenAlex `abstract_inverted_index` correctly inverted to plain text
- [ ] Abstract fallback chain implemented: S2 → OpenAlex inverted → S2 TLDR → `unavailable`
- [ ] **Provider adapters are the only writers to `source_store`** (HR-1) — enforced, not just documented

### CP-5 — Review *(B2)*
- [ ] Claim extraction produces atomic claims, each carrying `span_id` + `anchor_ids` + a `citability` score
- [ ] All three candidate strategies live: S2 `/snippet/search`, S2 Recommendations seeded with the paper's own cited works, OpenAlex search + one-hop `cited_by`/`references` expansion
- [ ] Reciprocal-rank fusion, dedupe by DOI/S2 id, subtract everything already cited
- [ ] Rerank scores candidates against **the claim**, not the topic
- [ ] Verifier returns one of: `supports`, `partially_supports`, `does_not_address`, `contradicts`, `unverifiable_no_abstract`
- [ ] Every non-`unverifiable` verdict carries a verbatim quote, and **a mechanical substring check against the fetched abstract kills the finding if the quote is not present**
- [ ] The same verifier serves both callers: missing-work candidates and existing-anchor checking
- [ ] Findings stream over SSE, ordered by citability descending, with `verified / total` progress

### CP-6 — Agent core *(B3)*
- [ ] Planner emits `EditPlan` as structured output only — it cannot emit prose or raw text edits
- [ ] All seven operations implemented: `AddCitations`, `FindSupport`, `Shorten`, `RewriteSection`, `ReplaceCitation`, `MoveText`, `FreeformEdit`
- [ ] `FreeformEdit` requires `no_typed_op_applies` + a justification string, and its firing rate is logged
- [ ] Invariant kernel is **pure code with no LLM call**, and correctly separates REJECT from FLAG per Appendix A
- [ ] Detach → transform → reattach implemented; the text model never receives citation markers
- [ ] Anchors below the reattachment threshold produce a user-facing decision, never a deletion
- [ ] REJECT returns the reason to the planner, max 2 retries, then surfaces to the user
- [ ] Every approved change set commits a new IR version; every version is revertible
- [ ] **Optimistic locking:** commands and approvals carry `base_version`; a moved head fails the commit and triggers a re-plan rather than a silent overwrite (ADR-021)
- [ ] Reattachment uses `LLMClient.embed()` fingerprints from the side table; `REATTACH_ACCEPT` / `REATTACH_FLAG_FLOOR` read from config, never inlined

### CP-7 — Frontend *(F1)*
- [ ] First screen is the upload. No marketing landing page.
- [ ] Parse inspector shows structure + every reference with its confidence tier, colour-coded, with quarantined raw strings shown verbatim
- [ ] Review feed streams findings live, each linked to a real external URL, each showing its verification label and quote
- [ ] Edit console: command input, proposed-diff view, per-change approve/reject
- [ ] Orphaned-anchor prompts render as an explicit user decision
- [ ] Export downloads the revised `.tex`
- [ ] Every failure state from HR-3 has a designed, legible visual treatment
- [ ] Design system implemented per `design/design-system.md`; accessibility not sacrificed for aesthetics

### CP-8 — Verification *(T1)*
- [ ] Golden set: 5–8 real arXiv PDFs across IEEE / APA / ACM / Nature superscript
- [ ] Parsing metrics reported: reference recall, field precision, arbiter resolution rate, orphan count, mean round-trip style similarity
- [ ] Kernel adversarial suite: fake `source_id`, dropped anchor, unsupported new claim — each asserted REJECT
- [ ] Recorded provider fixtures make review and edit flows deterministic in CI
- [ ] `LLM_MODE=replay` recordings committed; CI runs with **zero live LLM or provider calls**, and a cache miss fails the build rather than hitting the network (ADR-018)
- [ ] Threshold sweep against the golden set for `ARBITER_ACCEPT`, `REPAIR_TRIGGER`, `REATTACH_ACCEPT` — report the chosen values with evidence (ADR-024)
- [ ] A killed worker mid-review surfaces as a failed job in the UI, not as an empty result (ADR-022)
- [ ] E2E: upload → parse → review → edit → approve → export
- [ ] **Honesty audit** — see the test engineer brief. This gates release.

---

## 5. Working protocol (all agents)

1. **Read `goal.md` → `decision.md` → `memory.md` before starting. In that order.**
2. Work only inside your owned paths.
3. Commit after each coherent feature or fix, with a message following the convention in `memory.md`.
4. When you learn something non-obvious — an API quirk, a version pin, a gotcha — append it to
   `memory.md`. Do not keep it in your head; your context will be cut.
5. When you need something from another agent's domain, add an **Interface Request** to `memory.md`
   and code against the Appendix A contract in the meantime. Never reach across the boundary.
6. When a design decision changes, add an ADR to `decision.md`. Superseding is allowed; silent
   drift is not.
7. Before claiming a checkpoint, re-read its acceptance criteria and verify each one. Paste evidence
   into `memory.md`.

---

## Appendix A — Frozen contracts

Authoritative. B1 materializes this as `services/api/app/core/contracts.py` in its first commit.
Any change requires an ADR in `decision.md` and a note in `memory.md`.

```python
from enum import Enum
from typing import Literal, Protocol
from pydantic import BaseModel, Field

# ---------- errors ----------
class MissingAPIKeyError(RuntimeError): ...       # HR-2 — raised at startup, never caught to degrade
class ProviderRateLimited(RuntimeError): ...
class ParseFailure(RuntimeError): ...
class KernelRejection(RuntimeError): ...

# ---------- sources ----------
class AbstractSource(str, Enum):
    S2 = "s2"; OPENALEX_INVERTED = "openalex_inverted"; TLDR = "tldr"; UNAVAILABLE = "unavailable"

class Provenance(BaseModel):
    provider: Literal["semantic_scholar", "openalex", "crossref"]
    endpoint: str
    retrieved_at: str
    external_url: str                              # must be a real, resolvable URL

class SourceRecord(BaseModel):
    source_id: str
    csl: dict                                      # CSL-JSON — the one canonical citation model
    provenance: Provenance                         # HR-1: proof this came from an HTTP response
    abstract: str | None = None
    abstract_source: AbstractSource = AbstractSource.UNAVAILABLE

class SourceStore(Protocol):
    """APPEND-ONLY. Only app/providers/* may call put(). HR-1."""
    def put(self, record: SourceRecord) -> str: ...
    def get(self, source_id: str) -> SourceRecord | None: ...
    def has(self, source_id: str) -> bool: ...

# ---------- document IR ----------
class CitationAnchor(BaseModel):
    anchor_id: str
    source_ids: list[str]                          # FK into SourceStore — validated by the kernel
    offset_in_span: int
    original_marker_text: str | None = None
    provenance_kind: Literal["parsed", "agent_added"] = "parsed"
    confidence: float = 1.0
    fingerprint_id: str | None = None              # FK → anchor_fingerprints table (ADR-017).
                                                   # Vectors are NEVER stored inline: they would
                                                   # duplicate per version and make structural
                                                   # diffs unreadable.
    locator: str | None = None
    prefix: str | None = None

class Span(BaseModel):
    id: str
    text: str                                      # text lives ONLY here
    citation_anchors: list[CitationAnchor] = Field(default_factory=list)

class Block(BaseModel):
    id: str
    type: Literal["paragraph", "equation", "figure", "table", "list"]
    order: int
    spans: list[Span] = Field(default_factory=list)
    placeholder_caption: str | None = None         # figures/tables/equations: caption only (ADR-008)

class Section(BaseModel):
    id: str; level: int; title: str; order: int
    blocks: list[Block] = Field(default_factory=list)

class QuarantineEntry(BaseModel):
    raw: str
    reason: Literal["parse_failed", "unresolved", "orphan_marker", "segmentation_failed"]
    page: int | None = None

class DocumentMeta(BaseModel):
    title: str | None = None
    style_id: str | None = None
    style_confidence: float | None = None
    style_ambiguous: bool = False

class Document(BaseModel):
    doc_id: str
    version: int
    metadata: DocumentMeta
    sections: list[Section] = Field(default_factory=list)
    quarantine: list[QuarantineEntry] = Field(default_factory=list)

# ---------- parsing ----------
class ConfidenceTier(str, Enum):
    RESOLVED = "resolved"; PARSED_UNRESOLVED = "parsed_unresolved"
    LOW_CONFIDENCE = "low_confidence"; QUARANTINED = "quarantined"
    ORPHAN_MARKER = "orphan_marker"

class ParsedReference(BaseModel):
    ref_id: str
    raw_string: str                                # always retained, verbatim
    csl: dict | None
    tier: ConfidenceTier
    parse_confidence: float
    agreement_score: float | None = None           # arbiter; accept at >= 0.85
    source_id: str | None = None

# ---------- review ----------
class Claim(BaseModel):
    claim_id: str; text: str; span_id: str
    anchor_ids: list[str] = Field(default_factory=list)
    citability: float                              # streaming order = descending citability

class Candidate(BaseModel):
    source_id: str
    strategy: Literal["s2_snippet", "s2_recommendations", "openalex_search", "openalex_graph"]
    fused_score: float
    rerank_score: float | None = None

class VerificationLabel(str, Enum):
    SUPPORTS = "supports"; PARTIALLY_SUPPORTS = "partially_supports"
    DOES_NOT_ADDRESS = "does_not_address"; CONTRADICTS = "contradicts"
    UNVERIFIABLE_NO_ABSTRACT = "unverifiable_no_abstract"

class Verification(BaseModel):
    label: VerificationLabel
    quote: str | None                              # MUST be a substring of the fetched abstract
    abstract_source: AbstractSource
    confidence: float

class Finding(BaseModel):
    finding_id: str
    kind: Literal["missing_work", "claim_citation_mismatch", "no_candidates_found"]
    claim: Claim
    source_id: str | None
    verification: Verification | None
    severity: Literal["high", "medium", "low", "info"]

# ---------- agent ----------
class OperationType(str, Enum):
    ADD_CITATIONS = "AddCitations"; FIND_SUPPORT = "FindSupport"
    SHORTEN = "Shorten"; REWRITE_SECTION = "RewriteSection"
    REPLACE_CITATION = "ReplaceCitation"; MOVE_TEXT = "MoveText"
    FREEFORM_EDIT = "FreeformEdit"

class Operation(BaseModel):
    op: OperationType
    target_ids: list[str]
    params: dict = Field(default_factory=dict)
    no_typed_op_applies: bool = False              # required True for FREEFORM_EDIT
    justification: str | None = None               # required for FREEFORM_EDIT

class EditPlan(BaseModel):
    plan_id: str
    operations: list[Operation]

class ProposedChange(BaseModel):
    change_id: str
    op: Operation
    new_fragment: dict                             # partial IR
    new_source_ids: list[str] = Field(default_factory=list)
    orphaned_anchor_ids: list[str] = Field(default_factory=list)
    rationale: str

class KernelVerdict(BaseModel):
    decision: Literal["accept", "reject", "flag"]
    reasons: list[str]                             # never empty for reject/flag
    flags: list[str] = Field(default_factory=list)

# ---------- providers ----------
class Provider(Protocol):
    """Implementations whose API degrades silently without credentials MUST raise
    MissingAPIKeyError at construction when theirs is absent (HR-2). One whose API
    throttles with an error status we already raise on MAY run unauthenticated —
    see ADR-010a, and read it before adding a second exception."""
    async def search_works(self, query: str, limit: int = 10) -> list[SourceRecord]: ...
    async def match_reference(self, title: str, year: int | None = None) -> SourceRecord | None: ...
    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]: ...
    async def batch_hydrate(self, ids: list[str]) -> list[SourceRecord]: ...

# ---------- LLM (ADR-015 / 016 / 018) ----------
class LLMRole(str, Enum):
    """Model is chosen per ROLE, never globally. IDs pinned in config.py — no model
    string appears anywhere else in the codebase."""
    REPAIR = "repair"                  # gpt-5.4-mini  — reference segment-and-label
    CLAIM_EXTRACTION = "claim_extraction"  # gpt-5.4
    RERANK = "rerank"                  # gpt-5.4-mini  — after the embedding prefilter
    VERIFY = "verify"                  # gpt-5.5       — accuracy-critical, do not economise
    PLAN = "plan"                      # gpt-5.5
    TRANSFORM = "transform"            # gpt-5.4       — rewriting the user's prose
    ORCHESTRATE = "orchestrate"        # gpt-5.5       — tool-call routing over a conversation

class LLMClient(Protocol):
    """The ONLY path to OpenAI. Structured output is mandatory for every data-returning
    call — JSON Schema, not prompt-and-parse. Honours LLM_MODE=record|replay|live;
    in replay a cache miss RAISES rather than calling the API (ADR-018)."""
    async def complete(
        self, role: LLMRole, prompt: str, schema: dict, *, system: str | None = None
    ) -> dict: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...   # 3-small @ 512 dims

class JobStatus(str, Enum):
    QUEUED = "queued"; RUNNING = "running"; SUCCEEDED = "succeeded"; FAILED = "failed"

class Job(BaseModel):
    """A crashed worker is a FAILURE and must be visible. A UI streaming nothing forever
    reads as 'no findings' — the same false negative as ADR-010. HR-3."""
    job_id: str
    kind: Literal["ingest", "review"]
    status: JobStatus
    progress_current: int = 0
    progress_total: int = 0
    error: str | None = None
```

### Kernel rules (normative)

**REJECT** — invalid, discarded, reason returned to the planner:
1. any `source_id` in the change is not present in `source_store` *(HR-1)*
2. the document's `source_id` multiset shrinks without an approved removal operation *(HR-5)*
3. a newly asserted claim has no anchor carrying a `SUPPORTS`/`PARTIALLY_SUPPORTS` verification
4. IR schema violation
5. Pandoc refuses to render the resulting document

**FLAG** — valid but uncertain, shown to the user with the warning attached:
1. an anchor reattached below the similarity threshold
2. an anchor found no home after a transform (→ user decision, never deletion)
3. a cited source's verification label is weaker than `SUPPORTS`
