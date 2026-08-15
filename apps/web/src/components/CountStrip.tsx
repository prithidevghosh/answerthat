'use client';

import { Seal } from './Seal';
import { TIER_STATUS, TIER_COUNT_LABEL, TIER_ORDER, INK_TEXT } from '@/lib/status';
import type { TierCounts } from '@/lib/api/types';
import type { ConfidenceTier } from '@/lib/contracts';

/**
 * "38 resolved · 4 parsed, not found · 2 could not parse · 1 orphan marker"
 *
 * design-system.md §5: these counts are the honesty guarantee made visible, so
 * they get prominence, not a footnote. They are the first thing on the parse
 * screen and they are set in display type at the size of a headline.
 *
 * The reconciliation line below them is CP-2's arithmetic
 * (resolved + parsed_unresolved + low_confidence + quarantined == total
 * detected) stated on screen. If it ever fails to balance, the strip says so
 * loudly rather than quietly printing wrong numbers.
 */
export function CountStrip({
  counts,
  active,
  onSelect,
}: {
  counts: TierCounts;
  active: ConfidenceTier | 'all';
  onSelect: (tier: ConfidenceTier | 'all') => void;
}) {
  const tallied =
    counts.resolved + counts.parsed_unresolved + counts.low_confidence + counts.quarantined;
  const balances = tallied === counts.total_detected;

  const shown = TIER_ORDER.filter((t) => counts[t] > 0);

  return (
    <section aria-labelledby="counts-heading">
      <h2 id="counts-heading" className="sr-only">
        Reference outcomes
      </h2>

      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-4">
        {shown.map((tier, i) => {
          const status = TIER_STATUS[tier];
          const isActive = active === tier;
          return (
            <span key={tier} className="flex items-baseline">
              {i > 0 && (
                <span aria-hidden="true" className="mx-3 text-xl text-[var(--rule-strong)]">
                  ·
                </span>
              )}
              <button
                type="button"
                onClick={() => onSelect(isActive ? 'all' : tier)}
                aria-pressed={isActive}
                className={`group flex items-baseline gap-2.5 rounded px-1 transition-colors duration-ink ease-ink ${
                  INK_TEXT[status.ink]
                } ${isActive ? 'bg-cobalt/[0.07]' : 'hover:bg-cobalt/[0.04]'}`}
              >
                <span className="font-display text-3xl leading-none tabular-nums">
                  {counts[tier]}
                </span>
                <span className="flex items-center gap-1.5 font-ui text-xs">
                  <Seal kind={status.seal} size={14} className="shrink-0" />
                  <span
                    className={`underline-offset-4 ${
                      isActive ? 'underline decoration-current' : 'decoration-transparent'
                    }`}
                  >
                    {TIER_COUNT_LABEL[tier]}
                  </span>
                </span>
              </button>
            </span>
          );
        })}
      </div>

      <p className="mt-6 font-ui text-2xs leading-relaxed text-muted">
        {balances ? (
          <>
            <span className="text-primary">{tallied}</span> references detected,{' '}
            <span className="text-primary">{tallied}</span> accounted for. Nothing was dropped —
            every reference in your bibliography appears in exactly one tier above
            {counts.orphan_marker > 0 && (
              <>
                , and {counts.orphan_marker === 1 ? 'one in-text marker' : `${counts.orphan_marker} in-text markers`}{' '}
                cite an entry that is not in it
              </>
            )}
            .
          </>
        ) : (
          // If this ever renders, the pipeline lost a reference. Say so.
          <span className="text-sanguine">
            Counts do not reconcile: {tallied} accounted for against {counts.total_detected}{' '}
            detected. {Math.abs(counts.total_detected - tallied)} reference
            {Math.abs(counts.total_detected - tallied) === 1 ? '' : 's'} unaccounted for. This is a
            defect — please report it.
          </span>
        )}
      </p>
    </section>
  );
}
