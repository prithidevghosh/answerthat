'use client';

import { useState } from 'react';
import { Plate } from './Plate';
import { Seal } from './Seal';
import { DiffText } from './DiffText';
import { AnchorSeals, type AnchorEntry } from './AnchorSeals';
import type { ReviewedChange } from '@/lib/api/types';
import type { SourceRecord } from '@/lib/contracts';

export type Decision = 'pending' | 'approved' | 'rejected';

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
  reviewed,
  sources,
  anchorSources,
  decision,
  onDecide,
  busy,
}: {
  reviewed: ReviewedChange;
  sources: Record<string, SourceRecord>;
  /** anchor_id → source_ids, from the orphaned-anchor decisions in the plan. */
  anchorSources: Record<string, string[]>;
  decision: Decision;
  onDecide: (approve: boolean) => void;
  busy: boolean;
}) {
  const { change, verdict } = reviewed;
  const fragment = change.new_fragment as { before?: string; after?: string };
  const [error, setError] = useState<string | null>(null);

  const anchors: AnchorEntry[] = [
    ...change.orphaned_anchor_ids.flatMap((id) => {
      const sids = anchorSources[id];
      // If the plan did not tell us which source this anchor carried, show the
      // anchor id rather than guessing at a citation.
      if (!sids || sids.length === 0) {
        return [{ anchor_id: id, source_id: id, fate: 'orphaned' as const }];
      }
      return sids.map((sid) => ({ anchor_id: id, source_id: sid, fate: 'orphaned' as const }));
    }),
    ...change.new_source_ids.map((sid, i) => ({
      anchor_id: `new-${i}`,
      source_id: sid,
      fate: 'added' as const,
    })),
  ];

  const accent =
    decision === 'approved'
      ? 'verdigris'
      : decision === 'rejected'
        ? 'sanguine'
        : verdict.decision === 'flag'
          ? 'sepia'
          : 'cobalt';

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

      {fragment.before !== undefined && fragment.after !== undefined && (
        <div className="mt-6">
          <p className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">Proposed change</p>
          <div className="mt-3 rounded border border-hair bg-paper-deep/50 px-5 py-4">
            <DiffText before={fragment.before} after={fragment.after} />
          </div>
        </div>
      )}

      <div className="mt-6">
        <p className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">Citations</p>
        <div className="mt-3">
          <AnchorSeals entries={anchors} sources={sources} />
        </div>
      </div>

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
              className="rounded border border-sanguine/45 px-5 py-2 font-ui text-xs text-sanguine transition-colors duration-ink ease-ink hover:bg-sanguine/[0.07] disabled:opacity-50"
            >
              Reject
            </button>
          </>
        ) : (
          <span
            className={`inline-flex items-center gap-2 font-ui text-xs ${
              decision === 'approved' ? 'text-verdigris' : 'text-sanguine'
            }`}
          >
            <Seal kind={decision === 'approved' ? 'filled' : 'broken'} size={16} />
            {decision === 'approved' ? 'Approved' : 'Rejected'}
          </span>
        )}
      </div>

      {error && (
        <p role="alert" className="mt-3 font-ui text-2xs text-sanguine">
          {error}
        </p>
      )}
    </Plate>
  );
}
