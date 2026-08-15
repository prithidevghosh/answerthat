'use client';

import { useState } from 'react';
import { Plate } from './Plate';
import { Seal } from './Seal';
import { shortLabel } from '@/lib/csl/render';
import type { AnchorResolution, OrphanedAnchorDecision } from '@/lib/api/types';
import type { SourceRecord } from '@/lib/contracts';

/**
 * An anchor that found no home after a transform.
 *
 * ADR-013 step 4: below the reattachment threshold, the anchor is raised to the
 * user as a decision — keep here / move to… / remove — and is never dropped.
 * So this card has three explicit buttons and no default. There is no "dismiss"
 * and no way to close it without choosing; leaving it undecided leaves it
 * visibly undecided.
 *
 * The similarity score and the threshold are both shown, because "we were not
 * confident enough" is only meaningful next to the number it failed to reach.
 */
export function OrphanedAnchorCard({
  decision,
  sources,
  resolved,
  onResolve,
  busy,
}: {
  decision: OrphanedAnchorDecision;
  sources: Record<string, SourceRecord>;
  resolved: AnchorResolution | null;
  onResolve: (res: AnchorResolution) => void;
  busy: boolean;
}) {
  const [confirmRemove, setConfirmRemove] = useState(false);

  const labels = decision.source_ids.map((sid) => {
    const rec = sources[sid];
    return rec ? shortLabel(rec.csl) : sid;
  });

  return (
    <Plate as="li" accent={resolved ? 'verdigris' : 'madder'} className="px-6 py-6 sm:px-8">
      <span
        className={`inline-flex items-center gap-2 font-ui text-xs font-medium ${
          resolved ? 'text-verdigris' : 'text-madder'
        }`}
      >
        <Seal kind={resolved ? 'filled' : 'dangling'} size={17} />
        {resolved ? 'Decision recorded' : 'This citation needs your decision'}
      </span>

      <p className="measure mt-4 text-xs leading-relaxed text-secondary">
        After the rewrite, we could not find a sentence this citation clearly belongs to. We will
        not guess and we will not drop it, so it is yours to place.
      </p>

      <dl className="mt-5 space-y-4">
        <div>
          <dt className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">Citation</dt>
          <dd className="mt-1 text-base text-primary">
            {labels.join(', ')}
            {decision.original_marker_text && (
              <span className="ml-2 font-mono text-xs text-muted">
                {decision.original_marker_text}
              </span>
            )}
          </dd>
        </div>

        <div>
          <dt className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">
            Where it used to sit
          </dt>
          <dd className="measure mt-1 border-l-2 border-[var(--rule-hair)] pl-4 text-base italic leading-relaxed text-secondary">
            {decision.former_context}
          </dd>
        </div>

        {decision.best_candidate && (
          <div>
            <dt className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">
              Closest match we found
            </dt>
            <dd className="measure mt-1 border-l-2 border-sepia/40 pl-4 text-base leading-relaxed text-primary">
              {decision.best_candidate.preview}
              <span className="mt-1 block font-ui text-2xs text-muted">
                similarity{' '}
                <span className="font-mono text-sepia">
                  {decision.best_candidate.similarity.toFixed(2)}
                </span>{' '}
                — below the{' '}
                <span className="font-mono">{decision.threshold.toFixed(2)}</span> threshold we
                require before moving a citation on your behalf
              </span>
            </dd>
          </div>
        )}
      </dl>

      {resolved ? (
        <p className="mt-6 border-t border-hair pt-5 font-ui text-xs text-verdigris">
          {resolved.decision === 'keep_here' && 'Kept in place.'}
          {resolved.decision === 'move_to' && 'Moved to the suggested sentence.'}
          {resolved.decision === 'remove' && 'Removed — you approved this removal.'}
        </p>
      ) : (
        <div className="mt-6 border-t border-hair pt-5">
          <p className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">Your decision</p>
          <div className="mt-3 flex flex-wrap gap-3">
            <button
              type="button"
              disabled={busy}
              onClick={() => onResolve({ decision: 'keep_here' })}
              className="rounded border border-indigo/45 px-5 py-2 font-ui text-xs text-indigo transition-colors duration-ink ease-ink hover:bg-indigo/[0.06] disabled:opacity-50"
            >
              Keep here
            </button>

            {decision.best_candidate && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  onResolve({
                    decision: 'move_to',
                    target_span_id: decision.best_candidate!.span_id,
                  })
                }
                className="rounded border border-indigo/45 px-5 py-2 font-ui text-xs text-indigo transition-colors duration-ink ease-ink hover:bg-indigo/[0.06] disabled:opacity-50"
              >
                Move to closest match
              </button>
            )}

            {/*
              Removal is the one destructive choice, and HR-5 only permits a
              shrinking citation set when the user explicitly approves it — so it
              asks twice and names the consequence.
            */}
            {confirmRemove ? (
              <span className="inline-flex flex-wrap items-center gap-3 rounded border border-madder/45 bg-madder/[0.05] px-4 py-2">
                <span className="font-ui text-2xs text-madder">
                  Remove this citation from the document?
                </span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onResolve({ decision: 'remove' })}
                  className="rounded border border-madder/50 px-3 py-1 font-ui text-2xs text-madder hover:bg-madder/[0.09] disabled:opacity-50"
                >
                  Yes, remove
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmRemove(false)}
                  className="font-ui text-2xs text-secondary underline underline-offset-2"
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                disabled={busy}
                onClick={() => setConfirmRemove(true)}
                className="rounded border border-madder/45 px-5 py-2 font-ui text-xs text-madder transition-colors duration-ink ease-ink hover:bg-madder/[0.07] disabled:opacity-50"
              >
                Remove
              </button>
            )}
          </div>
        </div>
      )}
    </Plate>
  );
}
