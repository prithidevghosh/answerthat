/**
 * TypeScript transcription of goal.md Appendix A — the frozen contracts.
 *
 * This file mirrors `services/api/app/core/contracts.py`. It is authoritative
 * for the frontend: every screen is built against these shapes, whether the
 * data comes from the live API or from the typed fixtures in lib/api/fixtures.
 *
 * Do not "improve" a shape here. If the frontend needs something these types
 * cannot express, that is an Interface Request in memory.md §5, not an edit.
 */

// ---------- sources ----------
export type AbstractSource = 's2' | 'openalex_inverted' | 'tldr' | 'unavailable';

export interface Provenance {
  provider: 'semantic_scholar' | 'openalex' | 'crossref';
  endpoint: string;
  retrieved_at: string;
  /** HR-1: must be a real, resolvable URL. The UI always links it. */
  external_url: string;
}

export interface SourceRecord {
  source_id: string;
  /** CSL-JSON — the one canonical citation model (HR-4). Never pre-formatted. */
  csl: CslJson;
  provenance: Provenance;
  abstract?: string | null;
  abstract_source: AbstractSource;
}

/** Minimal structural view of CSL-JSON. citation.js consumes the whole object. */
export interface CslJson {
  id?: string;
  type?: string;
  title?: string;
  author?: Array<{ family?: string; given?: string; literal?: string }>;
  issued?: { 'date-parts'?: number[][]; raw?: string };
  'container-title'?: string;
  volume?: string;
  issue?: string;
  page?: string;
  DOI?: string;
  URL?: string;
  publisher?: string;
  [key: string]: unknown;
}

// ---------- document IR ----------
export interface CitationAnchor {
  anchor_id: string;
  /** FK into SourceStore — validated by the kernel. */
  source_ids: string[];
  offset_in_span: number;
  original_marker_text?: string | null;
  provenance_kind: 'parsed' | 'agent_added';
  confidence: number;
  locator?: string | null;
  prefix?: string | null;
}

export interface Span {
  id: string;
  /** Text lives ONLY here. */
  text: string;
  citation_anchors: CitationAnchor[];
}

export type BlockType = 'paragraph' | 'equation' | 'figure' | 'table' | 'list';

export interface Block {
  id: string;
  type: BlockType;
  order: number;
  spans: Span[];
  /** figures/tables/equations: caption only (ADR-008). */
  placeholder_caption?: string | null;
}

export interface Section {
  id: string;
  level: number;
  title: string;
  order: number;
  blocks: Block[];
}

export type QuarantineReason =
  | 'parse_failed'
  | 'unresolved'
  | 'orphan_marker'
  | 'segmentation_failed';

export interface QuarantineEntry {
  raw: string;
  reason: QuarantineReason;
  page?: number | null;
}

export interface DocumentMeta {
  title?: string | null;
  style_id?: string | null;
  style_confidence?: number | null;
  style_ambiguous: boolean;
}

export interface DocumentIR {
  doc_id: string;
  version: number;
  metadata: DocumentMeta;
  sections: Section[];
  quarantine: QuarantineEntry[];
}

// ---------- parsing ----------
export type ConfidenceTier =
  | 'resolved'
  | 'parsed_unresolved'
  | 'low_confidence'
  | 'quarantined'
  | 'orphan_marker';

export interface ParsedReference {
  ref_id: string;
  /** Always retained, verbatim. Rendered in mono, never truncated. */
  raw_string: string;
  csl: CslJson | null;
  tier: ConfidenceTier;
  parse_confidence: number;
  /** Arbiter; accepted at >= 0.85. */
  agreement_score?: number | null;
  source_id?: string | null;
}

/** An in-text marker whose target reference does not exist (tier orphan_marker). */
/**
 * An in-text marker whose bibliography entry does not exist.
 *
 * There is no `ref_id` here on purpose: an orphan marker is defined by having no
 * reference, so `anchor_id` is its identity. This type used to declare `ref_id` and a
 * non-optional `snippet`, and the backend sent neither — `snippet.split()` then threw
 * and took the whole inspector down on the first paper that actually had one.
 */
export interface OrphanMarker {
  anchor_id: string;
  marker_text: string;
  span_id: string;
  section_id: string;
  target: string | null;
  reason: string;
  page: number | null;
  /** The sentence the marker sits in, resolved from the IR. Null when it could not
   *  be located — stated rather than substituted. */
  snippet: string | null;
  section_title: string | null;
}

// ---------- review ----------
export interface Claim {
  claim_id: string;
  text: string;
  span_id: string;
  anchor_ids: string[];
  /** Streaming order = descending citability. */
  citability: number;
}

export type VerificationLabel =
  | 'supports'
  | 'partially_supports'
  | 'does_not_address'
  | 'contradicts'
  | 'unverifiable_no_abstract';

export interface Verification {
  label: VerificationLabel;
  /** MUST be a verbatim substring of the fetched abstract (ADR-006). */
  quote: string | null;
  abstract_source: AbstractSource;
  confidence: number;
}

export type FindingKind = 'missing_work' | 'claim_citation_mismatch' | 'no_candidates_found';

export interface Finding {
  finding_id: string;
  kind: FindingKind;
  claim: Claim;
  source_id: string | null;
  verification: Verification | null;
  severity: 'high' | 'medium' | 'low' | 'info';
}

// ---------- agent ----------
export type OperationType =
  | 'AddCitations'
  | 'FindSupport'
  | 'Shorten'
  | 'RewriteSection'
  | 'ReplaceCitation'
  | 'MoveText'
  | 'FreeformEdit';

export interface Operation {
  op: OperationType;
  target_ids: string[];
  params: Record<string, unknown>;
  /** Required true for FreeformEdit (ADR-009). */
  no_typed_op_applies: boolean;
  justification?: string | null;
}

export interface ProposedChange {
  change_id: string;
  op: Operation;
  /** Partial IR. */
  new_fragment: Record<string, unknown>;
  new_source_ids: string[];
  orphaned_anchor_ids: string[];
  rationale: string;
}

export interface KernelVerdict {
  decision: 'accept' | 'reject' | 'flag';
  /** Never empty for reject/flag. */
  reasons: string[];
  flags: string[];
}
