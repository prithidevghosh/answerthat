/**
 * The boundary where a tool result stops being JSON and becomes a card.
 *
 * `tool_result.data` is whatever the registry's handler put in the envelope, so
 * it arrives as JSON and nothing more. This module is the one place that turns
 * it into a checked shape; past here, components receive a `ToolPayload` and
 * never a bag of unknowns. That matters more in this flow than in the
 * deterministic screens: there, a screen fetches one endpoint and knows what it
 * asked for, while here a single stream carries twenty different payloads and a
 * mismatch would surface as `undefined.toFixed` inside a card, mid-conversation.
 *
 * Two deliberate choices:
 *
 *  - **The discriminant is the tool's name**, which the registry owns, rather
 *    than a sniff of the payload's fields. Two tools returning similar-looking
 *    objects cannot be confused, and a new tool is a new case rather than a
 *    silent match against an old one.
 *
 *  - **The checks go exactly as deep as the cards dereference.** `FindingCard`
 *    reads `finding.claim.citability.toFixed(2)` without a guard, so the finding
 *    check goes that deep and no deeper. A full structural validator here would
 *    be a second transcription of Appendix A living beside the first, and the
 *    two would drift — which is the failure the note at the top of ./types.ts
 *    describes.
 *
 * A payload that does not check out is `{card: 'none'}`, and the tool line then
 * renders the `summary` the tool itself wrote. That is never nothing: the tool
 * ran, the agent read the summary, and the user reads the same sentence.
 */
import type {
  ChangeSetProposal,
  ChatConfirmation,
  CommitResult,
  DocumentOutlineData,
  EvidenceHit,
  ExportManifest,
  JsonObject,
  JsonValue,
  TierCounts,
  ToolPayload,
} from './types';
import type { Finding, OrphanMarker, ParsedReference, Section, SourceRecord } from '../contracts';

const isObject = (v: JsonValue | undefined): v is JsonObject =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

const isArray = (v: JsonValue | undefined): v is JsonValue[] => Array.isArray(v);

const isString = (v: JsonValue | undefined): v is string => typeof v === 'string';

const isNumber = (v: JsonValue | undefined): v is number =>
  typeof v === 'number' && Number.isFinite(v);

/** `null` and `undefined` are both "the API did not say", and both are fine. */
const optString = (v: JsonValue | undefined): string | null => (isString(v) ? v : null);
const optNumber = (v: JsonValue | undefined): number | null => (isNumber(v) ? v : null);
const optBool = (v: JsonValue | undefined): boolean => v === true;

/** An array of objects that all pass `each`. An empty array passes. */
const everyObject = (v: JsonValue | undefined, each: (o: JsonObject) => boolean): boolean =>
  isArray(v) && v.every((item) => isObject(item) && each(item));

// --- shape checks, one per domain type a card dereferences ---

const isTierCounts = (v: JsonValue | undefined): boolean =>
  isObject(v) &&
  isNumber(v.resolved) &&
  isNumber(v.parsed_unresolved) &&
  isNumber(v.low_confidence) &&
  isNumber(v.quarantined) &&
  isNumber(v.orphan_marker) &&
  isNumber(v.total_detected);

/** `FindingCard` reads the claim's citability unguarded, so this goes that deep. */
const isFinding = (o: JsonObject): boolean =>
  isString(o.finding_id) &&
  isString(o.kind) &&
  isObject(o.claim) &&
  isNumber(o.claim.citability) &&
  isString(o.claim.text);

/** `ReferenceCard` keys its status off the tier and shows `ref_id` verbatim. */
const isReference = (o: JsonObject): boolean => isString(o.ref_id) && isString(o.tier);

const isOrphanMarker = (o: JsonObject): boolean =>
  isString(o.marker_text) && (isString(o.section_id) || isString(o.section_title));

/** `DocumentStructure` walks sections → blocks → spans → anchors. */
const isSection = (o: JsonObject): boolean =>
  isString(o.id) && isString(o.title) && everyObject(o.blocks, (b) => isString(b.type));

/** Everything `ChangeCard` and `OrphanedAnchorCard` touch without a guard. */
const isEvaluatedChange = (o: JsonObject): boolean =>
  isObject(o.change) &&
  isString(o.change.change_id) &&
  isObject(o.change.op) &&
  isString(o.change.op.op) &&
  isObject(o.verdict) &&
  isString(o.verdict.decision) &&
  isArray(o.verdict.reasons) &&
  isObject(o.diff) &&
  isArray(o.diff.blocks) &&
  isObject(o.diff.citations) &&
  isArray(o.diff.citations.anchors) &&
  isArray(o.notes) &&
  everyObject(o.orphans, (x) => isString(x.anchor_id) && isArray(x.actions));

const isChangeSet = (v: JsonValue | undefined): boolean =>
  isObject(v) &&
  isString(v.change_set_id) &&
  isNumber(v.base_version) &&
  everyObject(v.changes, isEvaluatedChange) &&
  everyObject(v.rejected, (r) => isArray(r.reasons));

const isManifest = (v: JsonValue | undefined): boolean =>
  isObject(v) &&
  isString(v.filename) &&
  isNumber(v.version) &&
  isNumber(v.bibliography_entries) &&
  typeof v.exportable === 'boolean' &&
  everyObject(v.placeholder_blocks, (p) => isString(p.type) && isNumber(p.count));

const isCommitResult = (v: JsonValue | undefined): boolean =>
  isObject(v) &&
  typeof v.committed === 'boolean' &&
  isString(v.message) &&
  isArray(v.applied_change_ids) &&
  isObject(v.skipped);

const isSourceRecord = (v: JsonValue | undefined): boolean =>
  isObject(v) && isString(v.source_id) && isObject(v.csl);

/**
 * Tool name → card, with the payload checked against what that card needs.
 *
 * Tools absent from this switch are not a defect: `start_review` returns a job
 * id, `set_style` returns a confirmation, `list_claims` returns a list the agent
 * reads and summarises. A one-line sealed entry with the tool's own summary is
 * the right rendering for those, and inventing a card for every tool would put
 * a panel on screen every time the agent looked something up.
 */
export function readToolPayload(name: string, data: JsonObject | null): ToolPayload {
  if (!data) return { card: 'none' };

  switch (name) {
    case 'get_parse_progress':
      return isString(data.state)
        ? {
            card: 'parse_progress',
            data: {
              state: data.state as 'queued' | 'running' | 'complete' | 'failed',
              stage: optString(data.stage),
              fraction: optNumber(data.fraction),
              elapsed_s: optNumber(data.elapsed_s),
              error: optString(data.error),
            },
          }
        : { card: 'none' };

    case 'get_parse_report':
      return isTierCounts(data.counts)
        ? {
            card: 'parse_report',
            data: {
              doc_id: optString(data.doc_id) ?? '',
              // Checked immediately above; the cast is the payoff for the check.
              counts: data.counts as unknown as TierCounts,
              references: everyObject(data.references, isReference)
                ? (data.references as unknown as ParsedReference[])
                : null,
              orphan_markers: everyObject(data.orphan_markers, isOrphanMarker)
                ? (data.orphan_markers as unknown as OrphanMarker[])
                : null,
              reconciliation_notes: isArray(data.reconciliation_notes)
                ? data.reconciliation_notes.filter(isString)
                : null,
              style_id: optString(data.style_id),
            },
          }
        : { card: 'none' };

    case 'get_document_outline':
      return {
        card: 'outline',
        data: {
          doc_id: optString(data.doc_id) ?? '',
          title: optString(data.title),
          version: optNumber(data.version),
          // Sections that do not carry their blocks cannot drive
          // `DocumentStructure`, and an outline with every count reading zero
          // would be a worse lie than showing none. Empty means "not renderable
          // here", and the summary line carries the answer instead.
          sections: everyObject(data.sections, isSection)
            ? (data.sections as unknown as Section[])
            : [],
          block_count: optNumber(data.block_count),
          span_count: optNumber(data.span_count),
          is_draft: optBool(data.is_draft),
        },
      };

    case 'describe_review_plan':
      return isArray(data.strategies)
        ? {
            card: 'review_plan',
            data: {
              strategies: data.strategies.filter(isString),
              all_strategies: isArray(data.all_strategies)
                ? data.all_strategies.filter(isString)
                : null,
              rerank_keep: optNumber(data.rerank_keep),
              verify_keep: optNumber(data.verify_keep),
              citability_min: optNumber(data.citability_min),
              estimated_claims: optNumber(data.estimated_claims),
              estimated_duration_s: optNumber(data.estimated_duration_s),
              notes: isArray(data.notes) ? data.notes.filter(isString) : null,
            },
          }
        : { card: 'none' };

    case 'get_review_progress':
      return isNumber(data.verified) && isNumber(data.total)
        ? {
            card: 'review_progress',
            data: {
              state: optString(data.state),
              verified: data.verified,
              total: data.total,
              findings_emitted: optNumber(data.findings_emitted),
              candidates_considered: optNumber(data.candidates_considered),
              quote_check_failures: optNumber(data.quote_check_failures),
              unverifiable_no_abstract: optNumber(data.unverifiable_no_abstract),
              claims_without_candidates: optNumber(data.claims_without_candidates),
            },
          }
        : { card: 'none' };

    case 'list_findings':
      return everyObject(data.findings, isFinding)
        ? {
            card: 'findings',
            data: {
              findings: data.findings as unknown as Finding[],
              total: optNumber(data.total),
            },
          }
        : { card: 'none' };

    case 'get_finding':
      // One finding, rendered by the same card as a list of one.
      return isObject(data.finding) && isFinding(data.finding)
        ? {
            card: 'findings',
            data: { findings: [data.finding] as unknown as Finding[], total: 1 },
          }
        : { card: 'none' };

    case 'get_source':
      return isSourceRecord(data.source)
        ? { card: 'source', data: { source: data.source as unknown as SourceRecord } }
        : { card: 'none' };

    case 'search_evidence':
      return everyObject(
        data.results,
        (r) => isString(r.kind) && isString(r.ref_id) && isString(r.text) && isNumber(r.score),
      )
        ? {
            card: 'evidence',
            data: {
              results: data.results as unknown as EvidenceHit[],
              index_status: optString(data.index_status),
            },
          }
        : { card: 'none' };

    case 'read_section':
    case 'get_span':
      return isString(data.text)
        ? {
            card: 'section_text',
            data: {
              section_id: optString(data.section_id) ?? optString(data.span_id) ?? '',
              title: optString(data.title),
              text: data.text,
              is_draft: optBool(data.is_draft),
            },
          }
        : { card: 'none' };

    case 'propose_edit':
      return isChangeSet(data)
        ? { card: 'change_set', data: data as unknown as ChangeSetProposal }
        : isChangeSet(data.change_set)
          ? { card: 'change_set', data: data.change_set as unknown as ChangeSetProposal }
          : { card: 'none' };

    case 'commit_change_set':
    case 'revert_document':
      return isCommitResult(data)
        ? { card: 'commit', data: data as unknown as CommitResult }
        : { card: 'none' };

    case 'get_export_manifest':
      return isManifest(data)
        ? { card: 'export_manifest', data: data as unknown as ExportManifest }
        : isManifest(data.manifest)
          ? { card: 'export_manifest', data: data.manifest as unknown as ExportManifest }
          : { card: 'none' };

    case 'export_latex':
      return isString(data.filename) && isString(data.download_url)
        ? {
            card: 'exported_file',
            data: {
              filename: data.filename,
              byte_size: optNumber(data.byte_size) ?? 0,
              download_url: data.download_url,
              style_id: optString(data.style_id),
              style_uncertain: optBool(data.style_uncertain),
            },
          }
        : { card: 'none' };

    default:
      return { card: 'none' };
  }
}

/**
 * The runtime names a confirmation after the *proposal*; this file names it
 * after the tool that will consume it.
 *
 * `runtime.py` emits `kind: "change_set"` and `kind: "export"` — the thing being
 * shown — while the frontend switches on `commit_change_set` and `export_latex`,
 * the thing being authorised. Both are reasonable and neither is wrong, so the
 * two vocabularies are reconciled here, at the boundary, exactly as the `done`
 * / `complete` disagreement is in the client.
 *
 * **This is not cosmetic.** An unmapped kind falls through to `unrecognised`,
 * and `unrecognised` has no orphan list — so a change set arriving under a name
 * this table does not know would render a Yes button that is *enabled while
 * citation anchors are still undecided*. That is HR-5 failing silently on the
 * one screen built to enforce it. Add a row here before adding a kind.
 */
const NORMALISE_KIND: Record<string, string> = {
  change_set: 'commit_change_set',
  commit_change_set: 'commit_change_set',
  export: 'export_latex',
  export_latex: 'export_latex',
  revert: 'revert_document',
  revert_document: 'revert_document',
  style: 'set_style',
  set_style: 'set_style',
};

/**
 * `awaiting_confirmation` → a proposal the screen can render.
 *
 * An unrecognised kind falls to `unrecognised` rather than being dropped: the
 * agent is asking to do something, and a Yes button over an invisible proposal
 * is the one outcome this gate exists to prevent.
 */
export function readConfirmation(raw: JsonObject): ChatConfirmation {
  const proposal = isObject(raw.proposal) ? raw.proposal : raw;
  const kind = NORMALISE_KIND[optString(raw.kind) ?? ''] ?? optString(raw.kind) ?? '';

  switch (kind) {
    case 'commit_change_set':
      if (isChangeSet(proposal)) {
        return { kind: 'commit_change_set', proposal: proposal as unknown as ChangeSetProposal };
      }
      break;

    case 'export_latex':
      if (isManifest(proposal)) {
        return { kind: 'export_latex', proposal: proposal as unknown as ExportManifest };
      }
      break;

    case 'revert_document':
      if (isNumber(proposal.to_version)) {
        return {
          kind: 'revert_document',
          proposal: {
            doc_id: optString(proposal.doc_id) ?? '',
            to_version: proposal.to_version,
            current_version: optNumber(proposal.current_version) ?? 0,
          },
        };
      }
      break;

    case 'set_style':
      if (isString(proposal.style_id)) {
        return {
          kind: 'set_style',
          proposal: {
            doc_id: optString(proposal.doc_id) ?? '',
            style_id: proposal.style_id,
            current_style_id: optString(proposal.current_style_id),
          },
        };
      }
      break;
  }

  return { kind: 'unrecognised', name: kind, proposal };
}
