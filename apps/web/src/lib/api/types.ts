/**
 * HTTP transport shapes.
 *
 * Appendix A freezes the *domain* models but not the wire surface, so these are
 * the request/response envelopes. They began as F1's proposal to B3 (memory.md
 * §5); where B3 has since served a shape of its own, **the API's shape is the
 * one written here** — the edit console shipped against the proposal, and a type
 * that describes a body the API does not send is worse than no type at all. It
 * type-checks, so nothing catches it until the browser reads `undefined`.
 *
 * The fixtures in ./fixtures are built to exactly these shapes, so the two
 * clients stay interchangeable.
 */
import type {
  DocumentIR,
  Finding,
  KernelVerdict,
  Operation,
  OrphanMarker,
  ParsedReference,
  ProposedChange,
  Section,
  SourceRecord,
} from '../contracts';

/** The honesty guarantee made visible — parse inspector count strip. */
export interface TierCounts {
  resolved: number;
  parsed_unresolved: number;
  low_confidence: number;
  quarantined: number;
  orphan_marker: number;
  /** CP-2: resolved + parsed_unresolved + low_confidence + quarantined must equal this. */
  total_detected: number;
}

export interface StyleCandidate {
  style_id: string;
  /** Normalised round-trip similarity (ADR-011). Shown, never hidden. */
  score: number;
}

export interface StyleDetection {
  style_id: string | null;
  score: number | null;
  /** Top two within 0.05 → the user must pick. */
  ambiguous: boolean;
  candidates: StyleCandidate[];
}

export interface ParseResult {
  document: DocumentIR;
  references: ParsedReference[];
  orphan_markers: OrphanMarker[];
  counts: TierCounts;
  /** Null when the style service is not bound — `/parse` composes this from B1's
   *  detector and returns null rather than omitting the key. Declaring it non-nullable
   *  is what turned that into a client-side crash on the orphan-marker path. */
  style: StyleDetection | null;
}

export type UploadStage = 'uploading' | 'extracting' | 'parsing' | 'resolving' | 'complete';

/**
 * `GET /documents/{docId}/parse-status`.
 *
 * The parse inspector needs this as well as the upload screen: a paper can be
 * opened by URL while its ingest is still running, and "not finished yet" has to
 * be distinguishable from "failed" and from "does not exist".
 */
export interface ParseStatus {
  state: 'queued' | 'running' | 'complete' | 'failed';
  stage: string | null;
  progress: number | null;
  version: number | null;
  error: string | null;
}

/**
 * `POST /documents` → 202.
 *
 * `version` is nullable because the 202 genuinely does not know it. The upload
 * is accepted, GROBID has not run, and the IR store has not assigned anything
 * yet — `waitForParse` is what fills it in, off `parse-status`, once the IR is
 * written. It was non-nullable while `uploadPdf` did both halves in one call;
 * splitting them (so the agentic path can navigate on the 202) makes the gap
 * visible, and a number invented to close it would be a lie about which version
 * a screen is reading.
 */
export interface UploadAccepted {
  doc_id: string;
  job_id: string;
  version: number | null;
}

export interface UploadProgress {
  stage: UploadStage;
  /** 0..1, or null when the stage cannot report a fraction honestly. */
  fraction: number | null;
  /**
   * A line beside the bar, or null when there is nothing to add.
   *
   * Nullable since `waitForParse` was split out of `uploadPdf`: it is given a
   * doc id, not a `File`, so it cannot report the filename and does not pretend
   * to. The caller holds the file and fills it back in.
   */
  detail: string | null;
}

// ---------- review stream ----------
/**
 * `POST /documents/{docId}/review` → 202.
 *
 * `stream` and `poll` are the API's own URLs for this job, and the client follows
 * them rather than composing its own. They are optional only because a very old
 * API build omitted them; `subscribeReview` falls back to the document-scoped
 * path, which is the one the router has always served.
 */
export interface ReviewStarted {
  job_id: string;
  doc_id: string;
  /** e.g. `/api/documents/doc_x/review/stream` — origin-relative, `/api` included. */
  stream?: string | null;
  poll?: string | null;
}

export interface ReviewProgress {
  verified: number;
  total: number;
}

export type ReviewEvent =
  | { type: 'progress'; data: ReviewProgress }
  | { type: 'finding'; data: Finding }
  | { type: 'done'; data: { verified: number; total: number } }
  /** HR-3: a stream that dies says so. It never just stops. */
  | { type: 'error'; data: { message: string; recoverable: boolean } };

export interface ReviewHandle {
  close(): void;
}

// ---------- edit console ----------
/**
 * `POST /documents/{docId}/commands` → the API's `ProposedChangeSet`.
 *
 * Nothing in it has been applied. The set is approved in **one** later request
 * carrying the `base_version` it was composed against (ADR-021), which is why
 * the id and the version live on this type rather than being re-derived by the
 * console: an approval that lands on whatever the head happens to be is the
 * silent lost update the optimistic lock exists to prevent.
 */
export interface CommandResult {
  change_set_id: string;
  doc_id: string;
  base_version: number;
  command: string;
  plan_id: string | null;
  /** `failed` is a real answer, not an error — `rejected` says why (HR-3). */
  status: 'awaiting_approval' | 'failed';
  attempts: number;
  changes: EvaluatedChange[];
  rejected: RejectedOperation[];
  message: string | null;
}

/**
 * A change that survived the kernel, with its verdict, its diff and its orphans.
 *
 * The API also sends a `context` field — the executor's evidence for the kernel
 * (derived spans, verifications, reattachment records). It is deliberately absent
 * here: it is an argument between the executor and the kernel, already settled by
 * the time the user sees a verdict, and nothing on this screen renders it.
 */
export interface EvaluatedChange {
  change: ProposedChange;
  verdict: KernelVerdict;
  diff: StructuralDiff;
  notes: string[];
  /**
   * Anchors this change could not reattach above threshold (ADR-013 step 4).
   * Per change, not per change set: an orphan belongs to the transform that
   * unhoused it, and approving that change is what makes the decision binding.
   */
  orphans: OrphanOption[];
}

export type OrphanAction = 'keep' | 'move' | 'remove';

/** An anchor raised as a decision. Never a deletion, never a default. */
export interface OrphanOption {
  anchor_id: string;
  marker: string | null;
  source_ids: string[];
  fingerprint_id: string | null;
  /** The closest home found, and the two bars it fell under. */
  best_span_id: string | null;
  best_span_text: string | null;
  score: number | null;
  threshold: number | null;
  flag_floor: number | null;
  actions: OrphanAction[];
}

/** An operation the kernel refused. Its reasons are shown, never swallowed (HR-3). */
export interface RejectedOperation {
  /** `null` → the plan itself was malformed, so there is no operation to name. */
  operation: Operation | null;
  reasons: string[];
  /** Which planner attempt this was (CP-6 allows 2 retries). */
  attempt: number;
}

// ---------- structural diff ----------
export interface SpanDelta {
  status: 'added' | 'removed' | 'modified' | 'unchanged';
  span_id: string;
  before_text: string | null;
  after_text: string | null;
  anchor_ids: string[];
}

export interface BlockDelta {
  status: 'added' | 'removed' | 'modified' | 'moved' | 'unchanged';
  block_id: string;
  before_section_id: string | null;
  after_section_id: string | null;
  spans: SpanDelta[];
}

export type AnchorStatus =
  | 'unchanged'
  | 'moved'
  | 'source_changed'
  | 'added'
  | 'held_for_decision'
  | 'removed';

export interface AnchorDelta {
  anchor_id: string;
  status: AnchorStatus;
  marker: string | null;
  before_span_id: string | null;
  after_span_id: string | null;
  source_ids_before: string[];
  source_ids_after: string[];
  note: string | null;
}

/** HR-5 made checkable: every anchor the change touched, and what became of it. */
export interface CitationLedger {
  preserved: boolean;
  total_before: number;
  total_after: number;
  sources_lost: Record<string, number>;
  sources_gained: Record<string, number>;
  anchors: AnchorDelta[];
  held_for_decision: string[];
}

export interface StructuralDiff {
  doc_id: string;
  base_version: number;
  citations: CitationLedger;
  blocks: BlockDelta[];
}

// ---------- approval ----------
export interface OrphanDecision {
  anchor_id: string;
  action: OrphanAction;
  /** Required for `move`. */
  target_span_id?: string | null;
}

/** `POST /change-sets/{id}/approve`. One request commits the whole set. */
export interface ApprovalPayload {
  base_version: number;
  approved_change_ids: string[];
  rejected_change_ids: string[];
  orphan_decisions: OrphanDecision[];
}

export interface CommitResult {
  committed: boolean;
  doc_id: string;
  base_version: number;
  new_version: number | null;
  applied_change_ids: string[];
  /** change_id → why it could not be applied. Reported, never dropped quietly. */
  skipped: Record<string, string>;
  diff: StructuralDiff | null;
  verdict: KernelVerdict | null;
  message: string;
}

// ---------- export ----------
export interface ExportManifest {
  doc_id: string;
  version: number;
  filename: string;
  /** ADR-008 scope cut, stated plainly rather than discovered by the user. */
  placeholder_blocks: { type: 'figure' | 'table' | 'equation'; count: number }[];
  bibliography_entries: number;
  style_id: string | null;
  /**
   * ADR-030: the style was used, but two candidates scored within the margin and
   * detection could not separate them. Not an error — a disclosure.
   */
  style_uncertain: boolean;
  /** False when something must be decided before a .tex can be rendered at all. */
  exportable: boolean;
  /** Present exactly when `exportable` is false, in the API's own words. */
  blocked_reason: string | null;
}

// ---------- the conversational flow ----------
/**
 * Shapes for `app/api/routes/chat.py`, written against the event table in
 * `backend_agentic.md` §7.
 *
 * Two rules govern everything below.
 *
 * **Every payload gets a real interface.** A `Record<string, unknown>` that
 * reaches a component turns a wire mismatch into a runtime read of `undefined`
 * — the same failure the note at the top of this file describes, one level
 * further in. So the tool results are a union discriminated on the tool's
 * *name*, narrowed once at the boundary in ./chat-payloads, and a component
 * only ever receives a shape that has already been checked.
 *
 * **A shape we do not recognise is still shown.** `ToolResult` always carries a
 * `summary` the tool wrote, so an unrecognised `data` renders that line rather
 * than nothing. Dropping a tool result because its payload changed would make
 * the agent look like it did less than it did.
 */

/** A JSON document, typed. Tool arguments are genuinely open — one schema per
 *  tool, decided by the registry — but they are not `any`, and this is what
 *  lets the arguments disclosure render them without guessing. */
export type JsonValue = string | number | boolean | null | JsonValue[] | { [k: string]: JsonValue };
export type JsonObject = { [k: string]: JsonValue };

/** `POST /documents/{doc_id}/chat` → 201/200. */
export interface Conversation {
  conversation_id: string;
  doc_id: string;
  /** e.g. `/api/chat/{id}/stream` — origin-relative, `/api` included. The
   *  client follows this rather than composing a path; see `subscribeChat`. */
  stream: string;
  poll: string;
}

export type ChatRole = 'user' | 'assistant' | 'tool' | 'system_notice';

/** One row of `chat_messages`, as `GET /api/chat/{id}` serves it. */
export interface ChatLogMessage {
  message_id: string;
  seq: number;
  role: ChatRole;
  content: string;
  /** Present on assistant messages that issued tool calls. */
  tool_calls: ChatToolCallPayload[] | null;
  /** Present on `role: 'tool'` messages — which call this answers. */
  tool_call_id: string | null;
  created_at: string | null;
}

/** `GET /api/chat/{conversation_id}` — the cold load. */
export interface ConversationLog {
  conversation_id: string;
  doc_id: string;
  status: string;
  messages: ChatLogMessage[];
}

// --- SSE payloads, one interface per event name ---

export interface ChatMessageStart {
  message_id: string;
  role: ChatRole;
}

export interface ChatMessageDelta {
  message_id: string;
  text: string;
}

export interface ChatMessageComplete {
  message_id: string;
  role: ChatRole;
  content: string;
}

export interface ChatToolCallPayload {
  call_id: string;
  name: string;
  arguments: JsonObject;
  /** A short human phrase from the registry — "Reading the parse report". It is
   *  the tool's own label, not a lookup table the frontend maintains, because a
   *  table here would go stale the moment a tool is added. */
  label: string;
}

export interface ChatToolResultPayload {
  call_id: string;
  name: string;
  ok: boolean;
  /** Always present. The factual line the model read. */
  summary: string;
  /** The structured payload a card is rendered from. */
  data: JsonObject | null;
  /** The envelope's `error` when the tool failed. Optional on the wire; the
   *  summary is the fallback, and one of the two is always shown in full. */
  error?: string | null;
}

/** `{kind: "parse", ...}` from the watcher, off `IngestPipeline.status()`. */
export interface ChatParseProgress {
  kind: 'parse';
  state: 'queued' | 'running' | 'complete' | 'failed' | null;
  /** The backend's own stage name — `references`, `arbiter`, `persist`. */
  stage: string | null;
  /** The real stage-position fraction. Null when the stage cannot report one. */
  fraction: number | null;
  filename: string | null;
  error: string | null;
}

/**
 * `{kind: "review", ...}` — the whole `ReviewStats` payload.
 *
 * All the secondary counters, not just `verified / total`. They are the
 * difference between "4 findings" and "4 findings, 11 candidates killed on the
 * quote check, 6 abstracts unavailable", which are different reports about the
 * same run.
 */
export interface ChatReviewProgress {
  kind: 'review';
  verified: number;
  total: number;
  findings_emitted: number | null;
  candidates_considered: number | null;
  quote_check_failures: number | null;
  unverifiable_no_abstract: number | null;
  claims_without_candidates: number | null;
  error: string | null;
}

export type ChatProgress = ChatParseProgress | ChatReviewProgress;

// --- the confirmation gate ---

/** What `propose_edit` returns and what a commit confirmation pins itself to. */
export interface ChangeSetProposal {
  change_set_id: string;
  doc_id: string;
  base_version: number;
  changes: EvaluatedChange[];
  rejected: RejectedOperation[];
  message: string | null;
}

export interface RevertProposal {
  doc_id: string;
  to_version: number;
  current_version: number;
}

export interface StyleProposal {
  doc_id: string;
  style_id: string;
  current_style_id: string | null;
}

/**
 * `awaiting_confirmation` — the structured proposal, so the screen renders the
 * real diff rather than the agent's summary of it.
 *
 * `unrecognised` is not a failure branch: a confirmation we cannot render as a
 * card is still a confirmation, and it is shown as the agent's question with the
 * raw proposal behind a disclosure. Swallowing it would leave a Yes button
 * approving something the screen never displayed.
 */
export type ChatConfirmation =
  | { kind: 'commit_change_set'; proposal: ChangeSetProposal }
  | { kind: 'export_latex'; proposal: ExportManifest }
  | { kind: 'revert_document'; proposal: RevertProposal }
  | { kind: 'set_style'; proposal: StyleProposal }
  | { kind: 'unrecognised'; name: string; proposal: JsonObject };

export interface ChatDone {
  message_id: string | null;
  tokens_used: number | null;
  budget_remaining: number | null;
}

export interface ChatFailure {
  /** The server's own words. Never replaced with a friendlier guess. */
  message: string;
  detail: string | null;
  /**
   * A *named* `error` event from the server is terminal and carries a reason. A
   * *bare* transport Event may reconnect. `subscribeReview` learned that the
   * hard way — treating the first as the second turned a named backend failure
   * into "Reconnecting…" forever.
   */
  recoverable: boolean;
}

export type ChatEvent =
  | { type: 'message_start'; data: ChatMessageStart }
  | { type: 'message_delta'; data: ChatMessageDelta }
  | { type: 'message'; data: ChatMessageComplete }
  | { type: 'tool_call'; data: ChatToolCallPayload }
  | { type: 'tool_result'; data: ChatToolResultPayload }
  | { type: 'progress'; data: ChatProgress }
  | { type: 'awaiting_confirmation'; data: ChatConfirmation }
  | { type: 'done'; data: ChatDone }
  | { type: 'error'; data: ChatFailure }
  | { type: 'heartbeat' };

export interface ChatHandle {
  close(): void;
}

// --- tool result payloads, one interface per tool that returns a card ---

export interface ParseProgressData {
  state: 'queued' | 'running' | 'complete' | 'failed';
  stage: string | null;
  fraction: number | null;
  elapsed_s: number | null;
  error: string | null;
}

export interface ParseReportData {
  doc_id: string;
  counts: TierCounts;
  /** Present only for `include: "full"`. */
  references: ParsedReference[] | null;
  orphan_markers: OrphanMarker[] | null;
  reconciliation_notes: string[] | null;
  style_id: string | null;
}

/**
 * `get_document_outline`. `is_draft` is the §5 guarantee: the IR published at
 * `tei_to_ir`, before references were reconciled, so the agent says "this is the
 * text as extracted" rather than presenting a half-finished paper as finished.
 */
export interface DocumentOutlineData {
  doc_id: string;
  title: string | null;
  version: number | null;
  sections: Section[];
  block_count: number | null;
  span_count: number | null;
  is_draft: boolean;
}

export interface ReviewPlanData {
  /** The strategies that will actually run for this document, introspected. */
  strategies: string[];
  all_strategies: string[] | null;
  rerank_keep: number | null;
  verify_keep: number | null;
  citability_min: number | null;
  estimated_claims: number | null;
  estimated_duration_s: number | null;
  notes: string[] | null;
}

export interface ReviewProgressData {
  state: string | null;
  verified: number;
  total: number;
  findings_emitted: number | null;
  candidates_considered: number | null;
  quote_check_failures: number | null;
  unverifiable_no_abstract: number | null;
  claims_without_candidates: number | null;
}

export interface FindingsData {
  findings: Finding[];
  total: number | null;
}

export interface SourceData {
  source: SourceRecord;
}

export interface EvidenceHit {
  kind: 'span' | 'abstract' | 'claim' | 'finding';
  ref_id: string;
  text: string;
  score: number;
}

export interface EvidenceSearchData {
  results: EvidenceHit[];
  /** "the index is still building, these results are partial" is a real answer;
   *  silently returning fewer hits is not. */
  index_status: string | null;
}

export interface SectionTextData {
  section_id: string;
  title: string | null;
  text: string;
  is_draft: boolean;
}

export interface ExportedFileData {
  filename: string;
  byte_size: number;
  download_url: string;
  style_id: string | null;
  style_uncertain: boolean;
}

/**
 * A tool result whose `data` has been checked against the tool it came from.
 *
 * The discriminant is the tool name, so a component switches on something the
 * registry owns rather than sniffing fields. `unrecognised` carries the summary
 * and nothing else — see the note at the top of this section.
 */
export type ToolPayload =
  | { card: 'parse_progress'; data: ParseProgressData }
  | { card: 'parse_report'; data: ParseReportData }
  | { card: 'outline'; data: DocumentOutlineData }
  | { card: 'review_plan'; data: ReviewPlanData }
  | { card: 'review_progress'; data: ReviewProgressData }
  | { card: 'findings'; data: FindingsData }
  | { card: 'source'; data: SourceData }
  | { card: 'evidence'; data: EvidenceSearchData }
  | { card: 'section_text'; data: SectionTextData }
  | { card: 'change_set'; data: ChangeSetProposal }
  | { card: 'commit'; data: CommitResult }
  | { card: 'export_manifest'; data: ExportManifest }
  | { card: 'exported_file'; data: ExportedFileData }
  | { card: 'none' };

// ---------- health (HR-2) ----------
export type ApiStatusKind = 'ok' | 'config_error' | 'unreachable';

export interface ApiStatus {
  kind: ApiStatusKind;
  /** Env vars the API reported as absent or empty. */
  missing_keys: string[];
  detail: string | null;
}

export type {
  SourceRecord,
  ParsedReference,
  DocumentIR,
  Finding,
  OrphanMarker,
  ProposedChange,
  Section,
};
