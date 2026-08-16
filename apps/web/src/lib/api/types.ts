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

export interface UploadAccepted {
  doc_id: string;
  version: number;
}

export interface UploadProgress {
  stage: UploadStage;
  /** 0..1, or null when the stage cannot report a fraction honestly. */
  fraction: number | null;
  detail: string;
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
  /** False when something must be decided before a .tex can be rendered at all. */
  exportable: boolean;
  /** Present exactly when `exportable` is false, in the API's own words. */
  blocked_reason: string | null;
}

// ---------- health (HR-2) ----------
export type ApiStatusKind = 'ok' | 'config_error' | 'unreachable';

export interface ApiStatus {
  kind: ApiStatusKind;
  /** Env vars the API reported as absent or empty. */
  missing_keys: string[];
  detail: string | null;
}

export type { SourceRecord, ParsedReference, DocumentIR, Finding, OrphanMarker, ProposedChange };
