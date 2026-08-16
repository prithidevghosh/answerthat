'use client';

import { useState } from 'react';
import { Plate } from './Plate';
import { Seal } from './Seal';
import { SideBySideDiff } from './DiffText';
import { AnchorSeals, type AnchorEntry } from './AnchorSeals';
import type { AnchorDelta, EvaluatedChange } from '@/lib/api/types';
import type { SourceRecord } from '@/lib/contracts';

export type Decision = 'pending' | 'approved' | 'rejected';

/**
 * The kernel's per-anchor verdict → the three seals the reader understands.
 *
 * `held_for_decision` is the one that must not be softened: it is an anchor with
 * nowhere to go, and calling it "kept" would make HR-5's guarantee unfalsifiable
 * on the one screen where it matters.
 */
const FATE_FROM_STATUS: Record<AnchorDelta['status'], AnchorEntry['fate']> = {
  unchanged: 'persisted',
  moved: 'persisted',
  source_changed: 'persisted',
  added: 'added',
  held_for_decision: 'orphaned',
  removed: 'removed',
};

const OP_LABEL: Record<string, string> = {
  AddCitations: 'Add citations',
  FindSupport: 'Find support',
  Shorten: 'Shorten',
  RewriteSection: 'Rewrite section',
  ReplaceCitation: 'Replace citation',
  MoveText: 'Move text',
  FreeformEdit: 'Freeform edit',
};

export function ChangeCard({
  evaluated,
  sources,
  decision,
  onDecide,
  busy,
}: {
  evaluated: EvaluatedChange;
  sources: Record<string, SourceRecord>;
  decision: Decision;
  onDecide: (approve: boolean) => void;
  busy: boolean;
}) {
  const { change, verdict, diff, notes } = evaluated;
  const [error, setError] = useState<string | null>(null);

  /**
   * The prose diff comes from the structural diff, not from `new_fragment`.
   *
   * `new_fragment` is the executor's instruction to the applier — `replace_spans`,
   * `move_blocks` — and its shape differs per operation. The diff is the thing
   * built for a reader: every touched span, with the text before and after.
   */
  /**
   * One comparison per paragraph, not per span.
   *
   * A `Shorten` does not rewrite sentences in place — it emits the old ones as
   * `removed` and the new ones as `added`, with no correspondence between them.
   * Rendering each span on its own produced dozens of half-empty cards ("this
   * text is new", "this text would be removed") and never once put a sentence
   * beside the thing that replaced it.
   *
   * Rebuilding both sides of the block is the honest fix. Before is every span
   * that existed; after is every span that survives. That is a true statement
   * about the paragraph, it reads as prose on both sides, and it invents no
   * sentence-level pairing that the diff never claimed. Unchanged spans are
   * included so each column is the real paragraph rather than a fragment of it.
   */
  const blockDiffs = diff.blocks
    .map((block) => ({
      block_id: block.block_id,
      before: block.spans
        .filter((s) => s.status !== 'added')
        .map((s) => s.before_text ?? '')
        .join(' ')
        .trim(),
      after: block.spans
        .filter((s) => s.status !== 'removed')
        .map((s) => s.after_text ?? '')
        .join(' ')
        .trim(),
      touched: block.spans.filter((s) => s.status !== 'unchanged').length,
    }))
    .filter((b) => b.touched > 0 && b.before !== b.after);

  // Anchors come from the citation ledger, which accounts for every one the
  // change touched — including the ones it left alone. That is the HR-5 claim,
  // and showing only the new and the broken would be showing only the good news.
  const anchors: AnchorEntry[] = diff.citations.anchors.flatMap((delta) => {
    const fate = FATE_FROM_STATUS[delta.status];
    const sids = delta.status === 'removed' ? delta.source_ids_before : delta.source_ids_after;
    const shown = sids.length > 0 ? sids : delta.source_ids_before;
    // An anchor whose sources we were not told is still an anchor. Show its id
    // rather than dropping it from a ledger that claims to be complete.
    if (shown.length === 0) return [{ anchor_id: delta.anchor_id, source_id: delta.anchor_id, fate }];
    return shown.map((sid) => ({ anchor_id: delta.anchor_id, source_id: sid, fate }));
  });

  const accent =
    decision === 'approved'
      ? 'verdigris'
      : decision === 'rejected'
        ? 'madder'
        : verdict.decision === 'flag'
          ? 'sepia'
          : 'indigo';

  return (
    <Plate as="li" accent={accent} className="px-6 py-6 sm:px-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">
          {OP_LABEL[change.op.op] ?? change.op.op}
        </span>
        <span className="font-mono text-2xs text-muted">{change.change_id}</span>
      </div>

      {/* The planner's reasoning, in its own words. */}
      <p className="measure mt-4 text-xs leading-relaxed text-secondary">{change.rationale}</p>

      {/* FreeformEdit is gated (ADR-009) and its use is disclosed, not hidden. */}
      {change.op.op === 'FreeformEdit' && change.op.justification && (
        <div className="mt-4 border-l-2 border-sepia/40 pl-4">
          <p className="font-ui text-2xs uppercase tracking-[0.12em] text-sepia">
            No typed operation applied
          </p>
          <p className="measure mt-1 text-xs leading-relaxed text-secondary">
            {change.op.justification}
          </p>
        </div>
      )}

      {blockDiffs.length > 0 && (
        <div className="mt-6">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">
              Proposed change
            </p>
            <p className="font-ui text-2xs text-muted">
              {blockDiffs.length} {blockDiffs.length === 1 ? 'paragraph' : 'paragraphs'} ·
              everything not shown is unchanged
            </p>
          </div>
          <div className="mt-3 space-y-3">
            {blockDiffs.map((block) => (
              <SideBySideDiff
                key={block.block_id}
                before={block.before}
                after={block.after}
              />
            ))}
          </div>
        </div>
      )}

      <div className="mt-6">
        <p className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">Citations</p>
        <div className="mt-3">
          <AnchorSeals entries={anchors} sources={sources} />
        </div>
        {/* The ledger's own one-line statement, in the API's words rather than a
            count this component re-derives and could get wrong. */}
        <p className="mt-2 font-ui text-2xs text-muted">
          {diff.citations.total_before} before · {diff.citations.total_after} after
          {!diff.citations.preserved && (
            <span className="text-madder"> — this change would shrink the citation set</span>
          )}
        </p>
      </div>

      {notes.length > 0 && (
        <ul className="mt-4 space-y-1">
          {notes.map((note, i) => (
            <li key={i} className="measure text-xs leading-relaxed text-secondary">
              {note}
            </li>
          ))}
        </ul>
      )}

      {/* Kernel flags are warnings attached to a valid change — shown, never
          folded into the rationale. */}
      {verdict.decision === 'flag' && verdict.reasons.length > 0 && (
        <div className="mt-6 rounded border border-sepia/40 bg-sepia/[0.05] px-5 py-4">
          <span className="inline-flex items-center gap-2 font-ui text-2xs font-medium text-sepia">
            <Seal kind="half" size={15} />
            Accepted with warnings
          </span>
          <ul className="mt-2 space-y-1">
            {verdict.reasons.map((r, i) => (
              <li key={i} className="measure text-xs leading-relaxed text-secondary">
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-hair pt-5">
        {decision === 'pending' ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setError(null);
                onDecide(true);
              }}
              className="rounded border border-verdigris/45 px-5 py-2 font-ui text-xs text-verdigris transition-colors duration-ink ease-ink hover:bg-verdigris/[0.07] disabled:opacity-50"
            >
              Approve
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setError(null);
                onDecide(false);
              }}
              className="rounded border border-madder/45 px-5 py-2 font-ui text-xs text-madder transition-colors duration-ink ease-ink hover:bg-madder/[0.07] disabled:opacity-50"
            >
              Reject
            </button>
          </>
        ) : (
          <span
            className={`inline-flex items-center gap-2 font-ui text-xs ${
              decision === 'approved' ? 'text-verdigris' : 'text-madder'
            }`}
          >
            <Seal kind={decision === 'approved' ? 'filled' : 'broken'} size={16} />
            {decision === 'approved' ? 'Approved' : 'Rejected'}
          </span>
        )}
      </div>

      {error && (
        <p role="alert" className="mt-3 font-ui text-2xs text-madder">
          {error}
        </p>
      )}
    </Plate>
  );
}
