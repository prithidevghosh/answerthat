/**
 * HTTP transport shapes.
 *
 * Appendix A freezes the *domain* models but not the wire surface, so these are
 * the frontend's proposed request/response envelopes. They are filed as an
 * Interface Request in memory.md §5 (F1 → B3). Until B3 lands the endpoints,
 * every screen runs against the typed fixtures in ./fixtures, which are built
 * to exactly these shapes — so wiring up the real API is a base-URL change.
 */
import type {
  DocumentIR,
  Finding,
  KernelVerdict,
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
  style: StyleDetection;
}

export type UploadStage = 'uploading' | 'extracting' | 'parsing' | 'resolving' | 'complete';

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
/** A change that survived the kernel, with the verdict attached. */
export interface ReviewedChange {
  change: ProposedChange;
  verdict: KernelVerdict;
}

/** A change the kernel refused. Its reasons are shown, never swallowed (HR-3). */
export interface RejectedOperation {
  op_summary: string;
  reasons: string[];
  /** How many planner retries were spent before surfacing (CP-6, max 2). */
  retries_spent: number;
}

export interface CommandResult {
  plan_id: string;
  changes: ReviewedChange[];
  rejected: RejectedOperation[];
  /** Anchors that found no home after a transform → explicit user decision. */
  orphaned_anchors: OrphanedAnchorDecision[];
}

export interface OrphanedAnchorDecision {
  anchor_id: string;
  source_ids: string[];
  original_marker_text: string | null;
  /** Sentence the anchor used to live in. */
  former_context: string;
  /** Best reattachment the system found, and why it fell short of threshold. */
  best_candidate: { span_id: string; preview: string; similarity: number } | null;
  threshold: number;
}

export type AnchorResolution =
  | { decision: 'keep_here' }
  | { decision: 'move_to'; target_span_id: string }
  | { decision: 'remove' };

// ---------- export ----------
export interface ExportManifest {
  doc_id: string;
  version: number;
  filename: string;
  /** ADR-008 scope cut, stated plainly rather than discovered by the user. */
  placeholder_blocks: { type: 'figure' | 'table' | 'equation'; count: number }[];
  bibliography_entries: number;
  style_id: string | null;
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
