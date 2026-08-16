'use client';

import { useState } from 'react';
import { Plate } from './Plate';
import { Seal } from './Seal';
import { shortLabel } from '@/lib/csl/render';
import type { OrphanDecision, OrphanOption } from '@/lib/api/types';
import type { SourceRecord } from '@/lib/contracts';

/**
 * An anchor that found no home after a transform.
 *
 * ADR-013 step 4: below the reattachment threshold, the anchor is raised to the
 * user as a decision — keep / move / remove — and is never dropped. So this card
 * has explicit buttons and no default. There is no "dismiss" and no way to close
 * it without choosing; leaving it undecided leaves it visibly undecided.
 *
 * The buttons offered are `option.actions`, not a list hard-coded here: the API
 * decides what is available for a given anchor, and offering a choice it will
 * refuse is a promise the screen cannot keep.
 *
 * The score and the threshold are both shown, because "we were not confident
 * enough" is only meaningful next to the number it failed to reach.
 */
export function OrphanedAnchorCard({
  option,
  sources,
  resolved,
  onResolve,
  busy,
}: {
  option: OrphanOption;
  sources: Record<string, SourceRecord>;
  resolved: OrphanDecision | null;
  onResolve: (decision: OrphanDecision) => void;
  busy: boolean;
}) {
  const [confirmRemove, setConfirmRemove] = useState(false);

  const labels = option.source_ids.map((sid) => {
    const rec = sources[sid];
    return rec ? shortLabel(rec.csl) : sid;
  });

  const canMove = option.actions.includes('move') && option.best_span_id !== null;

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
            {labels.length > 0 ? labels.join(', ') : option.anchor_id}
            {option.marker && (
              <span className="ml-2 font-mono text-xs text-muted">{option.marker}</span>
            )}
          </dd>
        </div>

        {option.best_span_text && (
          <div>
            <dt className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">
              Closest match we found
            </dt>
            <dd className="measure mt-1 border-l-2 border-sepia/40 pl-4 text-base leading-relaxed text-primary">
              {option.best_span_text}
              {option.score !== null && (
                <span className="mt-1 block font-ui text-2xs text-muted">
                  similarity <span className="font-mono text-sepia">{option.score.toFixed(2)}</span>
                  {option.threshold !== null && (
                    <>
                      {' '}
                      — below the <span className="font-mono">{option.threshold.toFixed(2)}</span>{' '}
                      threshold we require before moving a citation on your behalf
                    </>
                  )}
                </span>
              )}
            </dd>
          </div>
        )}
      </dl>

      {resolved ? (
        <p className="mt-6 border-t border-hair pt-5 font-ui text-xs text-verdigris">
          {resolved.action === 'keep' && 'Kept in place.'}
          {resolved.action === 'move' && 'Will move to the suggested sentence.'}
          {resolved.action === 'remove' && 'Will be removed — you approved this removal.'}
        </p>
      ) : (
        <div className="mt-6 border-t border-hair pt-5">
          <p className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">Your decision</p>
          <div className="mt-3 flex flex-wrap gap-3">
            {option.actions.includes('keep') && (
              <button
                type="button"
                disabled={busy}
                onClick={() => onResolve({ anchor_id: option.anchor_id, action: 'keep' })}
                className="rounded border border-indigo/45 px-5 py-2 font-ui text-xs text-indigo transition-colors duration-ink ease-ink hover:bg-indigo/[0.06] disabled:opacity-50"
              >
                Keep here
              </button>
            )}

            {canMove && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  onResolve({
                    anchor_id: option.anchor_id,
                    action: 'move',
                    target_span_id: option.best_span_id,
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
            {option.actions.includes('remove') &&
              (confirmRemove ? (
                <span className="inline-flex flex-wrap items-center gap-3 rounded border border-madder/45 bg-madder/[0.05] px-4 py-2">
                  <span className="font-ui text-2xs text-madder">
                    Remove this citation from the document?
                  </span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onResolve({ anchor_id: option.anchor_id, action: 'remove' })}
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
              ))}
          </div>
        </div>
      )}
    </Plate>
  );
}
