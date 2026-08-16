# backend_agentic.md — build the agentic orchestrator

You are adding a **second flow** to answerthat. The existing deterministic flow
(upload → parse → review → edit → export, with the user driving every step) stays exactly
as it is. Nothing in `app/parsing/`, `app/review/`, `app/agent/`, `app/export/` changes its
behaviour, and every existing route keeps its shape.

What you are building is a **conversational orchestrator** that sits above those packages
and drives them by tool call. The user talks to it; it decides what to do; it calls the
same functions the deterministic screens call.

Read `goal.md`, `decision.md` §ADR-006/009/010/013/014/015/018/021/022/024, and
`memory.md` §5 before writing code. The five hard rules (HR-1…HR-5 in `README.md`) apply
to everything below without exception.

---

## 0. The thing that would make this fake

Read this section twice. Most of the ways this feature can be built are wrong.

**The orchestrator is not a state machine with a chat skin.** These are all forbidden:

- Any `if parse_complete: send("Parsing is done, want to see it?")`. The agent composes
  every sentence it says. When parsing finishes, the runtime injects a *system notice*
  into the conversation and runs a turn; the model writes the message.
- Any keyword matching on user input (`if "review" in message: start_review()`).
  Routing is the model's job, expressed as a tool call, and nothing else.
- Any hardcoded description of what a review does. `describe_review_plan` introspects the
  live system — which retrieval strategies are actually available, whether
  `SEMANTIC_SCHOLAR_API_KEY` is set, what the current thresholds are — and returns facts.
  If the answer is not derived from runtime state, it is a lie waiting to happen.
- Any fixed ordering. The user may ask to export before reviewing, ask a question in the
  middle of a review, or ask for a second review of one section. The agent decides; the
  tools are individually callable in any order and each one validates its own
  preconditions and says why when they are not met.
- Any pre-written "response templates" the model fills in.

**The agent's competence comes from tools, not from prose in the system prompt.** If the
agent cannot answer a question about a finding, the fix is a tool that reads the finding,
not a paragraph in the prompt telling it to be helpful.

---

## 1. What exists that you will call

| Capability | Where it lives | Entry point |
|---|---|---|
| Ingest (GROBID → IR → repair → arbiter → style) | `app/parsing/pipeline.py` | `IngestPipeline.enqueue/status/parse_report/result` |
| Style detection & override | `app/parsing/style.py` | bound as `services.style` |
| Review (claims → candidates → rerank → verify) | `app/review/runner.py` | `ReviewJobRunner.start/status/stream` |
| Source records (append-only, HR-1) | `app/providers/source_store.py` | read-only via `SourceReader` port |
| Typed edit planning + invariant kernel | `app/agent/loop.py` | `Services.command_loop().run(document, command)` |
| Commit an approved change set | `app/agent/versioning.py` | `Services.versions().commit(change_set, ApprovalRequest)` |
| Export to LaTeX | `app/export/` | `services.exporter.to_latex(document)` |
| IR version store | `app/ir/store.py` | `services.documents` |
| The one path to OpenAI | `app/core/llm.py` | `OpenAILLMClient` |

Everything is already wired in `app/api/deps.py`. Bind your new collaborators there and
nowhere else.

---

## 2. Architectural rules you must not break

1. **`app/orchestrator/` must not import `app/parsing/`, `app/review/`, `app/agent/`,
   `app/providers/`, `app/ir/` or `app/export/`.** Declare Protocols in
   `app/orchestrator/ports.py` exactly as `app/agent/ports.py` does, and bind real
   implementations in `app/api/deps.py`. This is the rule that keeps the packages
   separable, and it is the first thing that gets quietly violated.
2. **No model ID outside `app/core/config.py`** (ADR-015). Add a new role, do not type a
   model string.
3. **No inline prompt strings.** Prompts live in `app/orchestrator/prompts/__init__.py`,
   following `app/agent/prompts/`.
4. **No threshold or limit outside `config.py`** (ADR-024). Max tool-call iterations,
   context window budget, retrieval `k` — all of them are settings.
5. **A missing collaborator is a 503 that names it, never a fallback.** `Services.require()`
   already does this. Do not add a degraded path.
6. **A failure is a visible state, never silence** (HR-3). Every tool returns a structured
   result that can express failure with a reason; a tool that raises has its exception
   turned into a tool result the model sees, not into a dropped turn.
7. Record an ADR in `decision.md` for each real decision here (next free number is
   **ADR-031**). At minimum: tool-calling in the LLM client, conversation persistence,
   the confirmation gate, and the evidence index.

---

## 3. `app/core/llm.py` — add tool-calling

The client today only does one thing: a single-shot structured-output call
(`complete(role, prompt, schema)`). An agent needs multi-turn messages and native tool
calls. Add them **to this client** — a second OpenAI call site anywhere in the codebase
loses per-role routing, the token budget, and record/replay in one move (ADR-015/018).

Add:

```python
@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict          # already JSON-parsed; malformed JSON raises StructuredOutputError

@dataclass
class AssistantTurn:
    text: str                # may be empty when the model only called tools
    tool_calls: list[ToolCall]
    finish_reason: str
    tokens: int

async def converse(
    self,
    role: LLMRole,
    messages: list[dict],            # OpenAI message dicts, incl. role="tool" results
    *,
    tools: list[dict] | None = None, # OpenAI tool schemas
    system: str | None = None,
    doc_id: str = "",
    on_text: Callable[[str], Awaitable[None]] | None = None,  # streamed deltas
) -> AssistantTurn: ...
```

Requirements:

- **Streaming.** When `on_text` is supplied, stream with `stream=True` and emit text deltas
  as they arrive, accumulating tool-call fragments (OpenAI sends tool call arguments in
  pieces — assemble by index, not by assuming one chunk per call). The user must see the
  agent typing; a six-minute review with a frozen chat is the same failure ADR-014 exists
  to prevent, one level up.
- **Record/replay still holds.** Extend `recording_key` to cover the full message list and
  the tool schemas. `LLM_MODE=replay` with no recording raises `LLMRecordingMissing`, as
  today. Record the assembled `AssistantTurn`, not the raw chunks.
- **The token budget still charges** (`self.budget.charge(doc_id, usage)`), and
  `TokenBudgetExceeded` propagates. It must reach the user as a visible chat message
  saying the document's budget is spent — never as a silently truncated conversation.
- **`finish_reason == "length"` is a refusal**, exactly as in `_call_openai` today.

Then:

- `app/core/contracts.py`: add **one** enum member `ORCHESTRATE = "orchestrate"` to
  `LLMRole`. That file is Appendix A verbatim — add the line, reformat nothing.
- `app/core/config.py`: add `model_orchestrate: str = "gpt-5.5"` with a comment saying why
  (tool-call routing over a long conversation is high-consequence and low-volume, same
  reasoning as `model_plan`), and add the role to `model_for()`.

---

## 4. `app/orchestrator/` — the new package

```
app/orchestrator/
├── __init__.py
├── ports.py         Protocols for everything from other packages
├── prompts/__init__.py   the system prompt and the notice templates
├── tools.py         the tool registry: schema + handler + policy per tool
├── runtime.py       the agent loop
├── session.py       conversation persistence (Postgres)
├── watcher.py       background job → conversation event bridge
└── index.py         the evidence index (embeddings + lookup)
```

### 4.1 `tools.py` — the tool registry

One declarative registry. Each entry carries:

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str          # what it does AND when not to use it
    schema: dict              # JSON Schema for arguments, strict
    handler: Callable[..., Awaitable[ToolResult]]
    mutating: bool = False    # writes a document version, or produces a file
    confirm: bool = False     # requires a user turn between proposal and execution
```

`ToolResult` is a small envelope: `{ok: bool, summary: str, data: dict, error: str | None}`.
`summary` is a short factual line the model reads; `data` is the structured payload the
frontend renders as a card. **Both are always returned** — the model must never have to
parse a card, and the UI must never have to parse prose.

The tools, grouped. Every one takes `doc_id` where relevant and validates it.

**Parsing**

- `get_parse_progress(doc_id)` → `{state, stage, fraction, elapsed_s, error}` straight from
  `IngestPipeline.status()`. `fraction` is the real stage-position fraction that
  `IngestRecord.progress` already computes — never a timer, never interpolated.
- `get_parse_report(doc_id, include: "counts" | "full" = "counts")` → the tier counts
  (`resolved / parsed_unresolved / low_confidence / quarantined / orphan_marker /
  total_detected`), and with `full`, the reference list, reconciliation notes and orphan
  markers. Refuses with a reason while the ingest is still running — a half-report reads
  as a paper with few references.
- `get_document_outline(doc_id)` → title, version, sections, block/span counts, and
  `is_draft: bool` (see §5).
- `get_style(doc_id)`, `set_style(doc_id, style_id)` — the latter is `mutating`, since it
  commits a document version (see `_persist_style` in `routes/documents.py`).

**Review**

- `describe_review_plan(doc_id)` → **introspected, not written down.** It must report:
  which retrieval strategies will actually run for this document
  (`CandidateGenerator.strategies_for(context)` vs `ALL_STRATEGIES`, so an absent
  Semantic Scholar key shows up as one fewer strategy rather than as thinner results); the
  current `RERANK_KEEP` / `VERIFY_KEEP` / `CITABILITY_MIN` from settings; how many claims
  the extractor is likely to produce (or, if you cannot know cheaply, say so rather than
  guessing); that every finding is quote-checked against the fetched abstract; and the
  expected duration given the ~1 req/s provider limit. This tool is what backs "tell the
  user how you will run the review before running it".
- `start_review(doc_id, section_ids=None, force=False)` → `ReviewJobRunner.start`. Returns
  the job id. Idempotent, exactly as today: a running or completed review of the same scope
  returns the existing job rather than billing a second pass.
- `get_review_progress(doc_id)` → the full `ReviewStats` payload: `verified`, `total`,
  `findings_emitted`, `candidates_considered`, `quote_check_failures`,
  `unverifiable_no_abstract`, `claims_without_candidates`. All of them. The whole point of
  those counters is that "4 findings" and "4 findings, 11 candidates killed on the quote
  check, 6 abstracts unavailable" are different reports.
- `list_findings(doc_id, kind=None, severity=None, limit=20, offset=0)`
- `get_finding(doc_id, finding_id)` → the finding with its claim text, verification label,
  verbatim quote, source record and external URL.

**Sources & question answering**

- `get_source(source_id)` → the `SourceRecord` from the append-only store: CSL-JSON,
  abstract, provenance, external URL. **Read-only. The orchestrator never writes to
  `source_store`** — HR-1 means a `source_id` exists only because a provider adapter saw
  it in an HTTP response.
- `search_evidence(doc_id, query, k=8, kinds=["span","abstract","claim","finding"])` →
  semantic search over the evidence index (§6). Returns snippets with their ids and kinds
  so the agent can cite what it is looking at.
- `read_section(doc_id, section_id)` / `get_span(doc_id, span_id)` → verbatim text from the
  IR, so the agent quotes the paper instead of paraphrasing from memory.
- `list_claims(doc_id, limit, offset)` → claims extracted by the review, with citability.

**Editing**

- `propose_edit(doc_id, instruction)` → `Services.command_loop().run(document, command)`,
  stored via `change_set_store().put(...)`. Returns the change-set id, base version, per
  change: the kernel verdict, the structural diff, the citation ledger, and any orphaned
  anchors with their scores. Writes nothing. Rejections come back with the kernel's reasons
  verbatim — the agent relays them, it does not soften them.
- `commit_change_set(change_set_id, approved_change_ids, rejected_change_ids,
  orphan_decisions)` → `Services.versions().commit(...)`. **`mutating=True, confirm=True`.**
- `revert_document(doc_id, to_version)` → `mutating=True, confirm=True`.

**Export**

- `get_export_manifest(doc_id, version=None)` → placeholder counts, bibliography size,
  style, `exportable`, `blocked_reason`. The ADR-008 placeholder disclosure is in here and
  the agent must pass it on before the user downloads anything.
- `export_latex(doc_id, version=None)` → renders through the exporter, returns
  `{filename, byte_size, download_url, style_id, style_uncertain}`. `confirm=True` because
  it hands the user a file. An `ExportFailure` becomes `ok=False` with the exporter's own
  message.

### 4.2 `runtime.py` — the agent loop

```
receive user message
  → append to conversation
  → loop up to settings.orchestrator_max_iterations:
      converse(ORCHESTRATE, messages, tools=registry.schemas(), on_text=emit_delta)
      if turn.tool_calls:
          for each call:  (execute in parallel when independent)
              enforce policy (§4.3)
              run handler → ToolResult
              emit tool_call + tool_result events
              append role="tool" message
          continue
      else:
          emit the assistant message, end turn
```

Rules:

- **The iteration cap is a setting**, and hitting it is a visible message ("I've taken N
  steps without finishing; here is where I got to"), not a silent stop.
- **A tool that raises is caught and returned to the model as `ok=False` with the
  exception's message.** The loop does not die because a provider 429'd. But the exception
  is also logged with its traceback — `runner.py` learned this the hard way.
- **Conversation trimming**: when the message history approaches the context budget, drop
  or summarise *old tool results*, never user messages and never the system prompt. Say in
  the log what was dropped.
- Every event goes to two places: the conversation's persisted log and its live subscriber
  queues. Same design as `ReviewJob` in `app/review/runner.py` — snapshot and subscribe
  under one lock so a reconnecting client loses nothing and duplicates nothing.

### 4.3 The confirmation gate

The product decision: **a chat confirmation is enough to commit.** There is no separate
approval screen in this flow. That places the whole burden on this gate, so build it
precisely:

1. A tool with `confirm=True` **may not execute in the same assistant turn that proposed
   it**. The runtime tracks, per conversation, the id of the last proposal (change set,
   export manifest, revert target). A `confirm` tool call is executed only if a **user
   message arrived after that proposal was shown**. Otherwise the call is refused with a
   `ToolResult` telling the model to present the proposal and ask first. This is
   mechanical, in the runtime, not a request in the prompt — a prompt-level rule is one
   jailbreak away from committing an edit nobody saw.
2. The proposal the user confirms must be the one they were shown: pin
   `change_set_id` + `base_version`. A moved head is the existing 409 (ADR-021), and the
   agent's job is to explain it and re-plan, not to retry against whatever the head became.
3. **Orphaned citation anchors are the one thing a plain "yes" cannot settle.** `commit`
   refuses (409) when an anchor is still undecided, and it must keep refusing. HR-5 says an
   anchor that cannot be reattached is raised to the user, never dropped. So the agent has
   to enumerate each orphan — its marker, the sentence it used to sit in, the best
   candidate home and the score it fell short by — and collect a `keep` / `move` / `remove`
   decision per anchor before calling `commit_change_set`. Do not add a default. Do not let
   the model choose on the user's behalf.
4. Emit an `awaiting_confirmation` event carrying the structured proposal, so the UI can
   render the real diff rather than the agent's summary of it.

### 4.4 `session.py` — persistence

Conversations survive a restart. New tables (`create_all()` at startup, ADR-020 — no
Alembic; register the module in `_TABLE_MODULES` in `app/api/main.py` or it silently will
not be created, see `memory.md` §4):

- `chat_conversations` — `conversation_id`, `doc_id`, `status`, `created_at`, `updated_at`
- `chat_messages` — `message_id`, `conversation_id`, `seq`, `role`
  (`user|assistant|tool|system_notice`), `content`, `tool_calls` JSONB, `tool_call_id`,
  `created_at`
- `chat_events` — the rendered event log for stream replay: `conversation_id`, `seq`,
  `event`, `payload` JSONB

Replay on reconnect is read from `chat_events`, so a browser refresh in the middle of a
review repaints the whole conversation and then follows live.

**While you are here, persist the parse report too.** It currently lives only in the
in-process `IngestRegistry`, so after an API restart `/parse` 404s for every document
ingested before it (a known limitation in `README.md`, and `memory.md` records it). The
agent's entire Q&A ability depends on that report existing. Write it as JSONB alongside the
document — a new `parse_reports` table keyed by `doc_id` and version — and have
`parse_report()` fall back to it when the registry has no record. This is in scope: without
it, a persisted conversation about an unpersisted parse is a conversation the agent cannot
continue.

### 4.5 `watcher.py` — background work becomes conversation

Parsing and review run as background asyncio tasks and know nothing about the chat. The
watcher bridges them, per conversation:

- Poll `get_parse_progress` on an interval (a setting) while the ingest is running; emit a
  `progress` event with `{kind: "parse", stage, fraction}` on every change. **This is a UI
  event only** — do not run an agent turn for each tick, or you will burn the token budget
  narrating a progress bar.
- Subscribe to `ReviewJobRunner.stream(doc_id)` while a review runs; forward `finding`,
  `progress` and `error` events to the conversation stream as `{kind: "review", ...}`.
- On **state transitions only** — parse complete, parse failed, review complete, review
  failed — append a `system_notice` message to the conversation stating the facts
  (`"Parsing finished. 47 references detected: 39 resolved, 5 parsed but unresolved,
  2 low confidence, 1 quarantined. 3 orphan markers."`) and **run an agent turn**. The
  agent writes the sentence the user reads. The notice is data; the message is the model's.
- The system notice for parse completion must explicitly instruct nothing and state
  everything. The behaviour the product wants — announce completion, *offer* the results,
  do not dump them — lives in the system prompt as a standing policy, not in the notice.

### 4.6 `prompts/__init__.py`

One system prompt. It must establish:

- **Role**: you are answerthat's research assistant for one specific paper. Name the
  doc_id and title.
- **Scope**: you answer questions about this paper, its references, the findings of its
  review, and the operations of this system. You do not answer general questions, write
  code, or discuss anything else — decline in one sentence and return to the paper. There
  are no tools that reach outside the paper, so scope discipline is mostly structural; the
  prompt closes the remaining gap.
- **Honesty**: never state a number you did not read from a tool result. Never claim a
  review found nothing when it has not finished. Never describe a citation you have not
  fetched. If a tool fails, say what failed. If parsing is still running, say that the
  bibliography is not final. These are the same guarantees the deterministic screens make
  visually, and the chat has to make them in words.
- **Standing behaviours** (as policy, not as a script):
  - While parsing runs, you may answer questions about the paper's text but you must state
    that references are not yet reconciled.
  - When parsing completes, tell the user it is done and summarise the tier counts — then
    *ask* whether they want to see the full parse result rather than printing it.
  - Before running a review, call `describe_review_plan` and tell the user, in your own
    words, exactly what will be done and roughly how long it takes. Run it only after they
    agree.
  - While a review runs, answer questions and report progress when asked. Do not spam.
  - Before any edit, show the proposed change, the citation ledger and any orphaned
    anchors, and ask. Orphaned anchors need a decision each; never choose for the user.
  - Before an export, state the placeholder disclosure from the manifest.
- **Tool discipline**: prefer a tool over recall; call several independent tools in one
  turn; never invent a `source_id`, `span_id`, `change_set_id` or `finding_id` — every id
  you use came out of a tool result.

Plus a small set of notice templates (parse complete / parse failed / review complete /
review failed / budget exhausted) that carry facts and no instructions.

---

## 5. Answering questions *during* parsing

The requirement is that the user can ask about the paper while it parses. Today that is
impossible: nothing about the document is readable until the ingest finishes and persists.

Fix it at the source. In `app/parsing/pipeline.py`, `ingest_tei` builds the full Document IR
at the `tei_to_ir` stage — sections, blocks, spans, title — long before references, repair,
arbitration and style. Publish it then:

- Add an `on_document: Callable[[Document], None] | None` callback to `ingest_tei`, invoked
  immediately after `tei_to_ir`.
- `IngestPipeline._run` passes a callback that stores it on the `IngestRecord` as
  `draft_document`.
- `IngestRegistry` exposes it; the orchestrator's `get_document_outline`, `read_section`,
  `get_span` and `search_evidence` serve the draft when the final IR is not yet persisted,
  **always with `is_draft: true` in the result**, so the agent says "this is the text as
  extracted; the bibliography is still being reconciled".

Before `tei_to_ir` completes (stages `queued` and `grobid`) the honest answer is that only
the filename is known, and the tools say exactly that. Do not fabricate an intermediate.

---

## 6. `index.py` — the evidence index

The agent must answer "why did you flag that?", "what else is in this reference?", "which
part of my paper does this finding attack?". Structured lookup covers most of it; semantic
search covers the rest.

- One table, `doc_embeddings`: `embedding_id`, `doc_id`, `kind`
  (`span | abstract | claim | finding`), `ref_id`, `text`, `vector` JSONB, `model`,
  `dimensions`, `created_at`. Mirror `app/ir/fingerprints.py` — insert-only, vectors as
  JSONB, embeddings via `LLMClient.embed()` at `settings.embedding_dimensions` (512,
  ADR-016). Do **not** add a second embedding model.
- Built in the background: paper spans once the parse completes; abstracts and claims as
  the review produces them. The build has a status, and `search_evidence` reports it —
  "the index is still building, these results are partial" is a real answer; silently
  returning fewer hits is not.
- Cosine similarity in Python over the document's own rows. A paper is a few hundred spans
  and a few hundred abstracts; this is milliseconds. **Do not add pgvector, a vector
  service, or a graph store.** Note in the ADR that pgvector is the scale path if a
  multi-document corpus ever exists.
- Search returns `{kind, ref_id, text, score}` so the agent can follow up with the exact
  tool for that kind. It is a router into structured data, not a substitute for it.

---

## 7. `app/api/routes/chat.py`

Follow the review route's shape exactly — the browser connects `EventSource` **directly**
to FastAPI, never through a Next.js route handler, or the stream buffers and the whole
thing looks broken (`memory.md` §3/§5).

| Method | Path | |
|---|---|---|
| `POST` | `/api/documents/{doc_id}/chat` | Create or return the conversation for this document. `201`/`200` with `{conversation_id, doc_id, stream, poll}` |
| `GET` | `/api/chat/{conversation_id}` | The persisted message log, for a cold page load |
| `POST` | `/api/chat/{conversation_id}/messages` | Send a user message → `202` with the stream URL. Returns immediately; the turn runs in the background |
| `GET` | `/api/chat/{conversation_id}/stream` | SSE. Replays the event log, then follows live |
| `POST` | `/api/chat/{conversation_id}/stop` | Cancel the in-flight turn. Emits a terminal event; the conversation stays usable |

SSE event names — keep them stable, the frontend prompt is written against these:

| Event | Payload |
|---|---|
| `message_start` | `{message_id, role}` |
| `message_delta` | `{message_id, text}` |
| `message` | `{message_id, role, content}` — the complete message |
| `tool_call` | `{call_id, name, arguments, label}` — `label` is a short human phrase from the registry, e.g. "Reading the parse report" |
| `tool_result` | `{call_id, name, ok, summary, data}` |
| `progress` | `{kind: "parse" \| "review", ...}` — from the watcher |
| `awaiting_confirmation` | `{kind, proposal}` — the structured change set / manifest |
| `error` | `{error, detail}` — terminal for the turn, never for the conversation |
| `done` | `{message_id, tokens_used, budget_remaining}` |
| `heartbeat` | `{}` every 15s |

Set the same headers the review stream sets (`Cache-Control: no-cache`,
`X-Accel-Buffering: no`), and register the router in `app/api/main.py`. Add the new
collaborators to the `/api/health` bound/unbound list — an unbound orchestrator must show
up there, not as a mysterious 503 later.

---

## 8. Wiring in `app/api/deps.py`

Add fields for `orchestrator`, `conversations`, `evidence_index`, and bind them in
`build_services()` with a `_bind_orchestrator(services, settings)` that follows the
existing pattern. Note the trap this file documents twice already
(`_bind_ingest`, `_bind_retrieval`): **a factory with an injection point cannot go through
the generic `_bind()` helper**, which passes only `settings`. The orchestrator needs the
tool registry, which needs nearly every other service. Build it explicitly, and if a
required collaborator is missing, leave it unbound with a logged reason so `require()` can
name it — do not construct a half-equipped agent.

---

## 9. Tests

Under `services/api/tests/unit/b4/`, matching the existing `b1/b2/b3` layout. Everything
runs against fakes; no network.

Cover at minimum:

- **The confirmation gate**: a model that emits `commit_change_set` in the same turn as
  `propose_edit` is refused, and nothing is written. Then the same call after a user
  message succeeds.
- **Orphans block a commit**: a change set with an undecided orphaned anchor cannot be
  committed even with an explicit confirmation, and the refusal names the anchor.
- **HR-1**: a tool result containing a `source_id` that is not in the store is rejected;
  no orchestrator path can write to `source_store`.
- **Tool failure is a turn, not a crash**: a handler that raises produces `ok=False` and
  the loop continues.
- **The iteration cap** terminates with a visible message.
- **Token budget exhaustion** surfaces as a chat error event, and the conversation stays
  readable.
- **Stream replay**: subscribing to a conversation mid-turn yields the full backlog and
  then live events, with nothing dropped or duplicated (the `ReviewJob.subscribe` property).
- **Draft-IR answering**: with the ingest at stage `references`, `get_document_outline`
  returns `is_draft: true`; at `grobid`, it honestly reports that nothing is readable yet.
- **`describe_review_plan` reflects reality**: with no Semantic Scholar key it reports
  three strategies, with one it reports four. Assert against
  `CandidateGenerator.strategies_for`, not against a fixed string.
- **Persistence**: a conversation written, the process's in-memory state discarded, and the
  conversation reloaded intact from Postgres.

Then run the existing suites — `pytest tests/unit -q`, `ruff check app tests`,
`mypy app` — inside the api container. Nothing existing may break.

---

## 10. Verification (do this, do not skip it)

1. `docker compose up --build`, wait for GROBID's healthcheck.
2. `curl localhost:8000/api/health` — the orchestrator and index must show as **bound**.
3. Upload a real PDF, create a conversation, and `curl -N` the SSE stream. Confirm text
   deltas arrive progressively — not as one clump at the end. A buffered stream is the
   single most likely thing to be wrong here.
4. Ask a question **while parsing is still running** and confirm the answer is grounded in
   the draft IR and says so.
5. Ask for a review; confirm the agent describes the plan from live introspection and waits.
6. Ask for an edit; confirm nothing is written until a second user message, and confirm an
   orphaned anchor blocks the commit until decided.
7. Export, then **compile the `.tex` with `pdflatex`** — not just `xelatex`, and not by
   grepping the file. That check has caught real breakage before.
8. Restart the API. The conversation must reload with its full history, and the parse
   report must still be readable.

Report honestly what passed and what did not. A feature that streams beautifully and
commits an unapproved edit is a failure, not a partial success.
