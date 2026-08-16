'use client';

import { Seal } from '@/components/Seal';
import type { ChatParseProgress, ChatReviewProgress } from '@/lib/api/types';

/**
 * Parse and review progress, from real events.
 *
 * The bar is a hairline that fills — `h-px bg-cobalt` — exactly as
 * `ProgressStrip` and the upload control do. Not a rounded track, not a spinner,
 * and never an animation invented to fill a silence: every width below comes
 * from a fraction the backend computed from its own stage position.
 *
 * The phase is always stated **in words** beside the bar. A counter must never
 * let in-progress read as complete, and a bar near its right-hand end is exactly
 * the thing that does.
 */

/**
 * The backend's stage vocabulary → a phrase.
 *
 * UI chrome, which the screen is allowed to supply — unlike anything attributed
 * to the agent, which always comes off the stream. A stage not in this table
 * renders its own name rather than a guess.
 */
const STAGE_PHRASE: Record<string, string> = {
  queued: 'Queued',
  grobid: 'Reading the document',
  tei_to_ir: 'Building the document structure',
  references: 'Segmenting references',
  repair: 'Repairing reference fields',
  arbiter: 'Reconciling references',
  style: 'Detecting the citation style',
  persist: 'Writing the document',
  complete: 'Complete',
};

export function ParseProgressCard({
  progress,
  settled = false,
}: {
  progress: ChatParseProgress;
  settled?: boolean;
}) {
  const failed = progress.state === 'failed';
  const complete = progress.state === 'complete';
  const pct = progress.fraction === null ? null : Math.round(progress.fraction * 100);

  const phrase = progress.stage
    ? (STAGE_PHRASE[progress.stage] ?? progress.stage)
    : 'Parsing';

  return (
    <section
      aria-label="Parse progress"
      className={`border-l-2 py-3 pl-5 ${
        failed ? 'border-madder' : complete ? 'border-verdigris' : 'border-cobalt'
      }`}
    >
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {failed && <Seal kind="broken" size={15} className="text-madder" />}
        {complete && <Seal kind="filled" size={15} className="text-verdigris" />}
        <span
          className={`engraved-label ${
            failed ? 'text-madder' : complete ? 'text-verdigris' : 'text-cobalt'
          }`}
        >
          {failed ? 'Parse failed' : complete ? 'Parsing complete' : phrase}
        </span>
        {!failed && pct !== null && (
          <span className="font-ui text-2xs text-muted">{pct}%</span>
        )}
        {!failed && pct === null && !complete && (
          <span className="font-ui text-2xs text-muted">
            this stage does not report progress
          </span>
        )}
        {progress.filename && (
          <span className="font-mono text-2xs text-muted">{progress.filename}</span>
        )}
      </p>

      {!failed && (
        <div className="mt-3 h-px w-full max-w-[560px] bg-[var(--rule-hair)]">
          {pct !== null && (
            <div
              className="h-px bg-cobalt transition-[width] duration-ink-slow ease-ink"
              style={{ width: `${complete ? 100 : pct}%` }}
            />
          )}
        </div>
      )}

      {/* The backend's own reason, verbatim. Never "something went wrong" — a
          parse failure is the one thing on this screen the user may be able to
          act on, and only the reason tells them how. */}
      {failed && progress.error && (
        <p className="measure mt-2 whitespace-pre-wrap break-words font-mono text-2xs leading-relaxed text-madder">
          {progress.error}
        </p>
      )}

      {!settled && !complete && !failed && (
        <p className="mt-2 font-ui text-2xs text-muted">
          References are not reconciled until this finishes, so any count before then would be
          wrong.
        </p>
      )}
    </section>
  );
}

export function ReviewProgressCard({
  progress,
  settled = false,
}: {
  progress: ChatReviewProgress;
  settled?: boolean;
}) {
  const complete = progress.total > 0 && progress.verified >= progress.total;
  const failed = progress.error !== null;
  const pct = progress.total > 0 ? Math.min(100, (progress.verified / progress.total) * 100) : 0;

  // The honest secondary counters. They are why a short findings list is
  // explicable rather than ambiguous, and they are only shown when the backend
  // sent them — a zero we invented would read as "nothing was discarded".
  const secondary = [
    progress.quote_check_failures !== null &&
      `${progress.quote_check_failures} candidate${progress.quote_check_failures === 1 ? '' : 's'} discarded on the quote check`,
    progress.unverifiable_no_abstract !== null &&
      `${progress.unverifiable_no_abstract} abstract${progress.unverifiable_no_abstract === 1 ? '' : 's'} unavailable`,
    progress.claims_without_candidates !== null &&
      `${progress.claims_without_candidates} claim${progress.claims_without_candidates === 1 ? '' : 's'} with no candidates`,
  ].filter((v): v is string => typeof v === 'string');

  return (
    <section
      aria-label="Review progress"
      className={`border-l-2 py-3 pl-5 ${
        failed ? 'border-madder' : complete ? 'border-verdigris' : 'border-cobalt'
      }`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <p className="flex items-baseline gap-3">
          <span className="font-display text-3xl leading-none tabular-nums text-primary">
            {progress.verified}
          </span>
          <span className="font-ui text-xs text-muted">
            of {progress.total} claims verified
          </span>
        </p>
        <p
          className={`flex items-center gap-2 font-ui text-xs ${
            failed ? 'text-madder' : complete ? 'text-verdigris' : 'text-cobalt'
          }`}
        >
          {failed && <Seal kind="broken" size={15} />}
          {complete && !failed && <Seal kind="filled" size={15} />}
          {failed ? 'Review failed' : complete ? 'Review complete' : 'Verifying claims'}
        </p>
      </div>

      <div className="mt-3 h-px w-full max-w-[560px] bg-[var(--rule-hair)]">
        <div
          className="h-px bg-cobalt transition-[width] duration-ink-slow ease-ink"
          style={{ width: `${pct}%` }}
        />
      </div>

      {progress.findings_emitted !== null && (
        <p className="mt-3 font-ui text-2xs text-secondary">
          {progress.findings_emitted} finding{progress.findings_emitted === 1 ? '' : 's'} so far
        </p>
      )}

      {secondary.length > 0 && (
        <p className="measure mt-1.5 font-ui text-2xs leading-relaxed text-muted">
          {secondary.join(' · ')}
        </p>
      )}

      {/*
        A failed review is not an empty one. `ReviewFeed`'s empty state draws
        this distinction and it has to hold here too: no claims verified means
        there is nothing to report either way, which is not a clean bill of
        health.
      */}
      {failed && (
        <>
          <p className="measure mt-3 text-xs leading-relaxed text-secondary">
            The run did not finish, so this is not a clean bill of health — it is a failed run.
          </p>
          {progress.error && (
            <p className="measure mt-2 whitespace-pre-wrap break-words font-mono text-2xs leading-relaxed text-madder">
              {progress.error}
            </p>
          )}
        </>
      )}

      {!settled && !complete && !failed && (
        <p className="mt-2 font-ui text-2xs text-muted">
          Findings arrive most-citable first, so the ones already shown matter most — but the list
          is not final yet.
        </p>
      )}
    </section>
  );
}
