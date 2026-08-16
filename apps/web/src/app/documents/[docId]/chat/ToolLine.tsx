'use client';

import { useState } from 'react';
import { Seal } from '@/components/Seal';
import type { SealKind } from '@/lib/status';
import type { ToolCallState, ToolState } from '@/lib/useChatStream';

/**
 * One thing the agent did, as a single line.
 *
 * A tool call is the agent touching the manuscript, and the user is entitled to
 * see it — but forty of them cannot each be a panel, so each is one line: a seal
 * for the state, the registry's own label in engraved caps, and the summary the
 * tool wrote. The arguments are behind a disclosure for anyone who wants them.
 *
 * State is carried by the seal **and** the text, never by ink alone (§7 rule 5).
 * The seals differ in shape, so the three states survive greyscale and a screen
 * reader hears the difference in words.
 */

const TONE: Record<ToolState, { seal: SealKind; ink: string; word: string }> = {
  // Half-struck: begun, not finished. A tool that never returns keeps this for
  // ever rather than ageing into something that reads as done.
  in_flight: { seal: 'half', ink: 'text-cobalt', word: 'Running' },
  ok: { seal: 'filled', ink: 'text-verdigris', word: 'Done' },
  failed: { seal: 'broken', ink: 'text-madder', word: 'Failed' },
};

export function ToolLine({ call }: { call: ToolCallState }) {
  const [showArgs, setShowArgs] = useState(false);
  const tone = TONE[call.state];
  const hasArgs = Object.keys(call.arguments).length > 0;

  return (
    <div className="border-l-2 border-[var(--rule-hair)] py-1 pl-4">
      <p className={`flex flex-wrap items-center gap-x-2.5 gap-y-1 ${tone.ink}`}>
        <Seal kind={tone.seal} size={14} className="shrink-0" />
        <span className="engraved-label">{call.label}</span>
        {/* The state in words as well as in the seal — a screen reader is told
            "Failed", not shown a colour it cannot see. */}
        <span className="sr-only">{tone.word}</span>
        {call.summary && (
          <span className="font-ui text-2xs text-secondary">{call.summary}</span>
        )}
      </p>

      {/* A failed tool shows its reason in full. Never truncated, never softened
          into "something went wrong" — the reason is the only actionable thing
          the failure carries. */}
      {call.state === 'failed' && call.error && (
        <p className="measure mt-1.5 whitespace-pre-wrap break-words font-mono text-2xs leading-relaxed text-madder">
          {call.error}
        </p>
      )}

      {hasArgs && (
        <>
          <button
            type="button"
            onClick={() => setShowArgs((v) => !v)}
            aria-expanded={showArgs}
            className="mt-1 font-ui text-2xs text-muted underline decoration-[var(--rule-fine)] underline-offset-2 hover:text-cobalt hover:decoration-cobalt"
          >
            {showArgs ? 'Hide arguments' : 'Arguments'}
          </button>
          {showArgs && (
            <pre className="mt-2 max-w-[720px] overflow-x-auto whitespace-pre-wrap break-words border border-hair bg-paper-deep px-4 py-3 font-mono text-2xs leading-relaxed text-secondary">
              {JSON.stringify(call.arguments, null, 2)}
            </pre>
          )}
        </>
      )}
    </div>
  );
}
