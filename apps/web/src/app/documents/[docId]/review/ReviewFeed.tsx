'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { FindingCard } from '@/components/FindingCard';
import { Plate } from '@/components/Plate';
import { Seal } from '@/components/Seal';
import { Fleuron } from '@/components/Ornament';
import { useReviewStream } from '@/lib/useReviewStream';
import { getClient } from '@/lib/api/client';
import type { SourceRecord } from '@/lib/contracts';

export function ReviewFeed({ docId, styleId }: { docId: string; styleId: string }) {
  const review = useReviewStream(docId, true);
  const [sources, setSources] = useState<Record<string, SourceRecord>>({});

  // Hydrate each finding's source as it arrives, so the card can show a real
  // citation and a real link. A source we cannot fetch leaves the card without
  // a link rather than inventing one.
  const neededIds = useMemo(
    () =>
      [...new Set(review.findings.map((f) => f.source_id).filter((x): x is string => !!x))].filter(
        (id) => !(id in sources),
      ),
    [review.findings, sources],
  );

  useEffect(() => {
    if (neededIds.length === 0) return;
    let live = true;
    const client = getClient();
    Promise.allSettled(neededIds.map((id) => client.getSource(id))).then((settled) => {
      if (!live) return;
      const next: Record<string, SourceRecord> = {};
      settled.forEach((s) => {
        if (s.status === 'fulfilled') next[s.value.source_id] = s.value;
      });
      if (Object.keys(next).length > 0) setSources((prev) => ({ ...prev, ...next }));
    });
    return () => {
      live = false;
    };
  }, [neededIds]);

  return (
    <main id="main" className="relative z-10 content-column py-16">
      <div className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <p className="font-ui text-2xs uppercase tracking-[0.14em] text-muted">Review</p>
          <h1 className="mt-2 font-display text-3xl text-primary">
            Findings, most citable first
          </h1>
        </div>
        <Link
          href={`/documents/${docId}/edit`}
          className="rounded border border-indigo/40 px-5 py-2.5 font-ui text-xs text-indigo transition-colors duration-ink ease-ink hover:bg-indigo/[0.06]"
        >
          Open edit console →
        </Link>
      </div>

      <ProgressStrip review={review} />

      <ul className="mt-12 space-y-6">
        {review.findings.map((f) => (
          <FindingCard
            key={f.finding_id}
            finding={f}
            source={f.source_id ? sources[f.source_id] : undefined}
            styleId={styleId}
          />
        ))}
      </ul>

      {review.findings.length === 0 && <EmptyState review={review} />}

      {review.phase === 'done' && review.findings.length > 0 && (
        <div className="mt-16">
          <Fleuron size={16} className="mx-auto text-indigo/40" />
          <p className="mt-4 text-center font-ui text-2xs text-muted">
            Review complete — {review.verified} of {review.total} claims verified,{' '}
            {review.findings.length} finding{review.findings.length === 1 ? '' : 's'}.
          </p>
        </div>
      )}
    </main>
  );
}

/**
 * `verified 23 / 47`, live.
 *
 * design-system.md §5: a counter that never lets in-progress read as complete.
 * The bar is a hairline that fills; the phase is always stated in words beside
 * it, so "streaming", "interrupted" and "complete" are never inferred from the
 * bar's position alone.
 */
function ProgressStrip({ review }: { review: ReturnType<typeof useReviewStream> }) {
  const { phase, verified, total } = review;
  const pct = total && total > 0 ? Math.min(100, (verified / total) * 100) : 0;

  const phaseLabel =
    phase === 'starting'
      ? 'Starting review'
      : phase === 'streaming'
        ? 'Verifying claims'
        : phase === 'interrupted'
          ? 'Stream interrupted'
          : phase === 'done'
            ? 'Complete'
            : phase === 'failed'
              ? 'Review failed'
              : 'Idle';

  const tone =
    phase === 'failed'
      ? 'text-madder'
      : phase === 'interrupted'
        ? 'text-sepia'
        : phase === 'done'
          ? 'text-verdigris'
          : 'text-indigo';

  return (
    <section aria-label="Review progress" className="mt-12">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <p className="flex items-baseline gap-3">
          <span className="font-display text-3xl leading-none tabular-nums text-primary">
            {verified}
          </span>
          <span className="font-ui text-xs text-muted">
            of {total ?? '—'} claims verified
          </span>
        </p>

        <p className={`flex items-center gap-2 font-ui text-xs ${tone}`} aria-live="polite">
          {phase === 'interrupted' && <Seal kind="half" size={15} />}
          {phase === 'failed' && <Seal kind="broken" size={15} />}
          {phase === 'done' && <Seal kind="filled" size={15} />}
          {phaseLabel}
        </p>
      </div>

      <div className="mt-4 h-px w-full bg-[var(--rule-hair)]">
        <div
          className="h-px bg-indigo transition-[width] duration-ink-slow ease-ink"
          style={{ width: `${pct}%` }}
        />
      </div>

      {review.message && (
        <p
          role="status"
          className={`mt-3 font-ui text-2xs ${phase === 'failed' ? 'text-madder' : 'text-sepia'}`}
        >
          {review.message}
          {phase === 'interrupted' && ' Findings already shown are unaffected.'}
        </p>
      )}

      {phase !== 'done' && phase !== 'failed' && (
        <p className="mt-3 font-ui text-2xs text-muted">
          This review is still running. Findings arrive most-citable first, so the ones above are
          the ones that matter most — but the list is not final yet.
        </p>
      )}
    </section>
  );
}

/**
 * Three genuinely different empty states. "Nothing yet" and "nothing found" are
 * different claims about the world and must never look alike (HR-3).
 */
function EmptyState({ review }: { review: ReturnType<typeof useReviewStream> }) {
  if (review.phase === 'failed') {
    return (
      <Plate accent="madder" className="mt-12 px-8 py-10">
        <span className="inline-flex items-center gap-3 font-ui text-xs font-medium text-madder">
          <Seal kind="broken" size={18} />
          The review could not run
        </span>
        <p className="measure mt-4 text-secondary">
          No claims were verified, so there is nothing to report either way. This is not a clean
          bill of health — it is a failed run.
        </p>
        {review.message && (
          <pre className="mt-4 overflow-x-auto whitespace-pre-wrap break-words rounded border border-hair bg-paper-deep px-4 py-3 font-mono text-2xs text-secondary">
            {review.message}
          </pre>
        )}
      </Plate>
    );
  }

  if (review.phase === 'done') {
    // The run completed and produced nothing. A real, positive result — and
    // stated as such, distinctly from "not finished yet".
    return (
      <Plate accent="verdigris" className="mt-12 px-8 py-10">
        <span className="inline-flex items-center gap-3 font-ui text-xs font-medium text-verdigris">
          <Seal kind="filled" size={18} />
          Review complete — no findings
        </span>
        <p className="measure mt-4 text-secondary">
          All {review.total} claims were checked against real search results, and none produced a
          finding: we did not identify missing work, and every cited source we could verify supports
          the claim it is attached to.
        </p>
        <p className="measure mt-3 text-xs leading-relaxed text-muted">
          This is a completed review with an empty result, not an empty screen.
        </p>
      </Plate>
    );
  }

  // Still running, nothing through yet.
  return (
    <div className="mt-16 flex flex-col items-center py-16 text-center">
      <Fleuron size={22} className="text-indigo/35" />
      <p className="mt-6 font-display text-xl text-primary">No findings yet</p>
      <p className="measure mt-3 text-xs leading-relaxed text-secondary">
        The review is running. Semantic Scholar allows about one request a second, so a full paper
        takes several minutes — findings will appear here as each claim finishes verifying, most
        citable first.
      </p>
    </div>
  );
}
