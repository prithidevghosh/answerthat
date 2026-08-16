'use client';

import { Fleuron } from '@/components/Ornament';
import { Seal } from '@/components/Seal';
import { AgentProse } from './AgentProse';
import { ToolLine } from './ToolLine';
import { ToolCard } from './ToolCard';
import { ParseProgressCard, ReviewProgressCard } from './ProgressCards';
import type { ChatTurn, TranscriptItem } from '@/lib/useChatStream';
import type { SourceRecord } from '@/lib/contracts';

/**
 * A printed dialogue, not a messaging app.
 *
 * Turns are told apart **typographically**, which is the whole design decision
 * here. There are no bubbles, no avatars, no right alignment and no per-message
 * timestamps. This is a record of a conversation about a manuscript and it reads
 * top to bottom in one column, the way a transcript is set — the user's turns in
 * the interface sans behind a cobalt rule, the agent's in the reading serif at
 * full measure, because the agent's prose *is* the body text of the page.
 *
 * Square corners, hairlines, nothing heavier than the §4 shadow ceiling. Every
 * chat UI you have ever seen is built from the opposite of all of this.
 */

export function Transcript({
  items,
  sources,
  styleId,
  streaming,
}: {
  items: TranscriptItem[];
  sources: Record<string, SourceRecord>;
  styleId: string;
  /** The message currently arriving, if any. Only that region is announced. */
  streaming: string | null;
}) {
  return (
    <div className="max-w-[860px] space-y-10">
      {items.map((item, i) => {
        const previous = items[i - 1];

        switch (item.kind) {
          case 'parse_settled':
            return (
              <Settled key={item.id}>
                <ParseProgressCard progress={item.progress} settled />
              </Settled>
            );

          case 'review_settled':
            return (
              <Settled key={item.id}>
                <ReviewProgressCard progress={item.progress} settled />
              </Settled>
            );

          case 'turn':
            return (
              <div key={item.id}>
                {/*
                  A rule where the speaker changes, and only there. A divider
                  between every message would be a table of contents for a
                  conversation.
                */}
                {previous?.kind === 'turn' &&
                  previous.turn.role !== item.turn.role &&
                  item.turn.role === 'user' && (
                    <div className="mb-10 flex items-center gap-4" aria-hidden="true">
                      <span className="h-px flex-1 bg-[var(--rule-hair)]" />
                      <Fleuron size={11} className="shrink-0 text-cobalt/35" />
                      <span className="h-px flex-1 bg-[var(--rule-hair)]" />
                    </div>
                  )}
                <Turn
                  turn={item.turn}
                  sources={sources}
                  styleId={styleId}
                  announced={streaming === item.turn.message_id}
                />
              </div>
            );
        }
      })}
    </div>
  );
}

function Settled({ children }: { children: React.ReactNode }) {
  return <div className="animate-rise-in">{children}</div>;
}

function Turn({
  turn,
  sources,
  styleId,
  announced,
}: {
  turn: ChatTurn;
  sources: Record<string, SourceRecord>;
  styleId: string;
  announced: boolean;
}) {
  if (turn.role === 'user') {
    return (
      <div className="animate-rise-in border-l-2 border-cobalt pl-5">
        <p className="whitespace-pre-wrap font-ui text-xs leading-relaxed text-secondary">
          {turn.content}
        </p>
      </div>
    );
  }

  if (turn.role === 'system_notice') {
    // Not the agent speaking. It is the runtime stating a fact — a parse
    // finished, a review failed — and the agent's own sentence about it is the
    // message that follows. Setting it apart keeps the two from being confused.
    return (
      <p className="animate-rise-in inline-flex items-center gap-2 font-ui text-2xs text-muted">
        <Seal kind="open" size={13} />
        {turn.content}
      </p>
    );
  }

  return (
    <div className="animate-rise-in">
      {turn.content && (
        <div
          // aria-live on the streaming region only. On the whole log, a screen
          // reader re-reads the entire conversation on every delta.
          aria-live={announced ? 'polite' : undefined}
        >
          {/* The model writes Markdown. Rendering it raw put literal `###` and
              backticks in the body text of the page — see AgentProse. */}
          <AgentProse text={turn.content} />
        </div>
      )}

      {turn.toolCalls.length > 0 && (
        <div className={`space-y-2 ${turn.content ? 'mt-5' : ''}`}>
          {turn.toolCalls.map((call) => (
            <ToolLine key={call.call_id} call={call} />
          ))}
        </div>
      )}

      {/* The structured results, as the real components rather than as the
          agent's description of them. */}
      {turn.toolCalls.some((c) => c.payload.card !== 'none') && (
        <div className="mt-6 space-y-6">
          {turn.toolCalls.map((call) => (
            <ToolCard
              key={`card-${call.call_id}`}
              payload={call.payload}
              sources={sources}
              styleId={styleId}
            />
          ))}
        </div>
      )}
    </div>
  );
}
