'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { MarginPlate, MarginFoot, type PlateNumber } from '@/components/Ornament';
import { WorkbenchHeader } from '@/components/WorkbenchHeader';
import { Plate } from '@/components/Plate';
import { Seal } from '@/components/Seal';
import { getClient } from '@/lib/api/client';
import { recallFlow } from '@/lib/flow';
import { useChatStream } from '@/lib/useChatStream';
import type { Conversation } from '@/lib/api/types';
import type { SourceRecord } from '@/lib/contracts';
import { Transcript } from './Transcript';
import { ParseProgressCard, ReviewProgressCard } from './ProgressCards';
import { Confirmation } from './Confirmation';
import { Composer } from './Composer';

/**
 * The conversational path, on one screen.
 *
 * **Nothing here decides what happens next.** There is no `if (parseDone)
 * startReview()`, no keyword match on what the user typed, and no call to
 * `/change-sets/{id}/approve`. The agent decides, on the server; this component
 * renders what it did and sends what the user said. Every sentence attributed to
 * the agent came off the stream — the only copy this file supplies is its own
 * chrome, and the only requests it makes are `sendMessage` and `stopTurn`.
 *
 * The margin plate follows the work rather than sitting on one number. The four
 * plates are the four stages, and this screen does all four in turn, so pinning
 * it to Pl. I would put the parse engraving beside a running review. It steps
 * with the live phase, which is the same fact stated twice that the guided
 * screens get from their route.
 */
export function ChatConsole({
  docId,
  styleId,
  title,
  version,
}: {
  docId: string;
  styleId: string;
  title: string | null;
  version: number | null;
}) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  /**
   * Did the user choose this route, or type the URL?
   *
   * It decides whether a conversation is opened on arrival. Choosing
   * "Conversational" on the threshold is a request for one; landing here from a
   * pasted link is not, and silently creating a conversation — which costs model
   * budget the moment the agent's first turn runs — because someone opened a
   * page is not a decision this screen gets to make for them.
   */
  const [chosen, setChosen] = useState<boolean | null>(null);

  useEffect(() => {
    setChosen(recallFlow(docId) === 'conversational');
  }, [docId]);

  const open = useCallback(() => {
    setStarting(true);
    setStartError(null);
    getClient()
      .startConversation(docId)
      .then(setConversation)
      .catch((err: unknown) =>
        setStartError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setStarting(false));
  }, [docId]);

  useEffect(() => {
    if (chosen === true && !conversation && !starting && !startError) open();
  }, [chosen, conversation, starting, startError, open]);

  const chat = useChatStream(conversation);
  const sources = useSources(useNeededSourceIds(chat.transcript, chat.pending));

  const composerRef = useRef<HTMLTextAreaElement>(null);
  const send = useCallback(
    (text: string) => {
      chat.send(text);
      // Focus returns to the composer after every send — including a send that
      // came from a confirmation button, where the button is about to disable
      // and would otherwise drop focus to the document body.
      composerRef.current?.focus();
    },
    [chat],
  );

  const plate = plateFor(chat);

  return (
    <>
      <MarginPlate plate={plate} />
      <div className="leaf">
        <WorkbenchHeader
          docId={docId}
          current="chat"
          title={title}
          version={version ?? undefined}
        />

        <main id="main" className="relative z-10 content-column py-16">
          <div className="max-w-[860px]">
            <p className="engraved-label text-muted">Conversation</p>
            <h1 className="mt-2 font-display text-3xl text-primary">
              Work on this paper by asking
            </h1>
          </div>

          {chosen === null ? null : !conversation ? (
            <NoConversation
              docId={docId}
              chosen={chosen}
              starting={starting}
              error={startError}
              onStart={open}
            />
          ) : chat.fatal ? (
            <FatalCard docId={docId} detail={chat.fatal} />
          ) : (
            <>
              <div className="mt-12">
                <Transcript
                  items={chat.transcript}
                  sources={sources}
                  styleId={styleId}
                  streaming={streamingId(chat)}
                />
              </div>

              {chat.transcript.length === 0 && chat.phase !== 'connecting' && <Opening />}

              {/* Thinking, said in this typeface. No typing dots — a half-struck
                  seal beside the word says the same thing and belongs here. */}
              {chat.phase === 'thinking' && (
                <p className="mt-8 inline-flex items-center gap-2 font-ui text-xs text-cobalt">
                  <Seal kind="half" size={15} />
                  Thinking
                </p>
              )}

              <StreamNotice chat={chat} />
            </>
          )}
        </main>

        {/*
          The dock. Opaque, with a hairline to sit on — no backdrop-blur and no
          translucency at any opacity (§4). Text passing under a frosted bar
          smears the rules beneath it and reads as a rendering fault.
        */}
        {conversation && !chat.fatal && (
          <div className="sticky bottom-0 z-20 border-t border-hair bg-paper">
            <div className="content-column py-5">
              <div className="max-w-[860px]">
                {chat.parse && <ParseProgressCard progress={chat.parse} />}
                {chat.review && (
                  <div className={chat.parse ? 'mt-5' : ''}>
                    <ReviewProgressCard progress={chat.review} />
                  </div>
                )}

                {chat.pending && (
                  <div className={chat.parse || chat.review ? 'mt-6' : ''}>
                    <Confirmation
                      confirmation={chat.pending}
                      sources={sources}
                      busy={chat.busy}
                      onSend={send}
                    />
                  </div>
                )}

                <Composer
                  ref={composerRef}
                  busy={chat.busy}
                  hint={hintFor(chat)}
                  onSend={send}
                  onStop={chat.stop}
                />
              </div>
            </div>
          </div>
        )}

        <MarginFoot plate={plate} />
      </div>
    </>
  );
}

type Chat = ReturnType<typeof useChatStream>;

/** The plate follows the work: I parse, II review, III edit, IV export. */
function plateFor(chat: Chat): PlateNumber {
  if (chat.pending?.kind === 'export_latex') return 4;
  if (chat.pending?.kind === 'commit_change_set') return 3;
  if (chat.review) return 2;
  if (chat.parse) return 1;

  for (let i = chat.transcript.length - 1; i >= 0; i--) {
    const item = chat.transcript[i];
    if (item.kind !== 'turn') continue;
    for (const call of item.turn.toolCalls) {
      if (call.name.includes('export')) return 4;
      if (call.name.includes('edit') || call.name.includes('change_set')) return 3;
      if (call.name.includes('review') || call.name.includes('finding')) return 2;
    }
  }
  return 1;
}

/** The message currently arriving, so only that region is announced. */
function streamingId(chat: Chat): string | null {
  for (let i = chat.transcript.length - 1; i >= 0; i--) {
    const item = chat.transcript[i];
    if (item.kind === 'turn' && !item.turn.complete) return item.turn.message_id;
  }
  return null;
}

/**
 * One line naming what the agent can do right now, from live state.
 *
 * Not a fixed string, and deliberately empty most of the time. Empty ivory is
 * the intended state (§7 rule 4) — the alternative is a row of suggestion chips,
 * which would be this screen telling the user what to want.
 */
function hintFor(chat: Chat): string | null {
  if (chat.phase === 'awaiting_confirmation') return null;
  if (chat.parse) return 'The bibliography is still reconciling';
  if (chat.review) return 'A review is running — you can ask about it, or about anything else';
  return null;
}

/**
 * Interrupted and failed are different claims about the world (HR-3).
 *
 * One may reconnect and the transcript is intact; the other ended a turn and
 * will not resume. They get different inks, different seals and different
 * sentences, and neither is allowed to look like the other.
 */
function StreamNotice({ chat }: { chat: Chat }) {
  if (!chat.notice) return null;
  const interrupted = chat.notice.kind === 'interrupted';

  return (
    <div
      role="status"
      className={`mt-10 max-w-[860px] border-l-2 py-3 pl-5 ${
        interrupted ? 'border-sepia' : 'border-madder'
      }`}
    >
      <p
        className={`inline-flex items-center gap-2 font-ui text-xs ${
          interrupted ? 'text-sepia' : 'text-madder'
        }`}
      >
        <Seal kind={interrupted ? 'half' : 'broken'} size={15} />
        {chat.notice.message}
      </p>
      {chat.notice.detail && (
        <p className="measure mt-2 whitespace-pre-wrap break-words font-mono text-2xs leading-relaxed text-secondary">
          {chat.notice.detail}
        </p>
      )}
      <p className="measure mt-2 font-ui text-2xs leading-relaxed text-muted">
        {interrupted
          ? 'Nothing already said is lost. The transcript above is unaffected.'
          : 'This ended the turn, not the conversation — you can carry on below.'}
      </p>
    </div>
  );
}

function Opening() {
  return (
    <p className="measure mt-12 font-body text-base leading-relaxed text-secondary">
      Ask about the paper, its references, or what the review found. Tell it what you want changed
      and it will show you the diff before anything is written.
    </p>
  );
}

function NoConversation({
  docId,
  chosen,
  starting,
  error,
  onStart,
}: {
  docId: string;
  chosen: boolean;
  starting: boolean;
  error: string | null;
  onStart: () => void;
}) {
  if (starting) {
    return (
      <p className="mt-12 inline-flex items-center gap-2 font-ui text-xs text-cobalt" role="status">
        <Seal kind="half" size={15} />
        Opening the conversation
      </p>
    );
  }

  return (
    <Plate accent={error ? 'madder' : 'cobalt'} className="mt-12 max-w-[860px] px-8 py-8">
      <span
        className={`inline-flex items-center gap-3 font-ui text-xs font-medium ${
          error ? 'text-madder' : 'text-cobalt'
        }`}
      >
        <Seal kind={error ? 'broken' : 'open'} size={18} />
        {error ? 'This conversation could not be opened' : 'This document has no conversation yet'}
      </span>

      <p className="measure mt-4 text-secondary">
        {error
          ? 'Your document is unchanged. Nothing was started.'
          : chosen
            ? 'Opening one starts an assistant on this paper.'
            : 'You arrived here by URL rather than by choosing this route, so nothing has been started — an assistant costs model budget from its first turn, and that is your call to make.'}
      </p>

      {error && (
        <pre className="measure mt-4 overflow-x-auto whitespace-pre-wrap break-words border border-hair bg-paper-deep px-4 py-3 font-mono text-2xs leading-relaxed text-secondary">
          {error}
        </pre>
      )}

      <div className="mt-8 flex flex-wrap gap-4">
        <button
          type="button"
          onClick={onStart}
          className="border border-cobalt/45 px-6 py-2.5 font-ui text-xs text-cobalt transition-colors duration-ink ease-ink hover:bg-cobalt/[0.06]"
        >
          {error ? 'Try again' : 'Start a conversation'}
        </button>
        <Link
          href={`/documents/${docId}/parse`}
          className="border border-hair px-6 py-2.5 font-ui text-xs text-secondary transition-colors duration-ink ease-ink hover:border-strong hover:text-primary"
        >
          Open the guided screens instead
        </Link>
      </div>
    </Plate>
  );
}

function FatalCard({ docId, detail }: { docId: string; detail: string }) {
  return (
    <Plate accent="madder" className="mt-12 max-w-[860px] px-8 py-8">
      <span className="inline-flex items-center gap-3 font-ui text-xs font-medium text-madder">
        <Seal kind="broken" size={18} />
        Could not load this conversation
      </span>
      <p className="measure mt-4 text-secondary">
        The conversation exists but its history did not come back. Nothing has been lost — this is a
        read that failed, not a conversation that was deleted.
      </p>
      <pre className="measure mt-4 overflow-x-auto whitespace-pre-wrap break-words border border-hair bg-paper-deep px-4 py-3 font-mono text-2xs leading-relaxed text-secondary">
        {detail}
      </pre>
      <Link
        href={`/documents/${docId}/parse`}
        className="mt-8 inline-block border border-hair px-6 py-2.5 font-ui text-xs text-secondary transition-colors duration-ink ease-ink hover:border-strong hover:text-primary"
      >
        Open the guided screens
      </Link>
    </Plate>
  );
}

/**
 * Every source id the transcript needs, so a citation renders as a citation.
 *
 * The same hydration `ReviewFeed` and `EditConsole` do: a record we cannot fetch
 * leaves the card without a link rather than inventing one.
 */
function useNeededSourceIds(
  transcript: Chat['transcript'],
  pending: Chat['pending'],
): string[] {
  return useMemo(() => {
    const ids = new Set<string>();

    const fromChangeSet = (changes: { orphans: { source_ids: string[] }[] }[]) => {
      changes.forEach((c) => c.orphans.forEach((o) => o.source_ids.forEach((s) => ids.add(s))));
    };

    transcript.forEach((item) => {
      if (item.kind !== 'turn') return;
      item.turn.toolCalls.forEach((call) => {
        const payload = call.payload;
        if (payload.card === 'findings') {
          payload.data.findings.forEach((f) => f.source_id && ids.add(f.source_id));
        } else if (payload.card === 'parse_report') {
          (payload.data.references ?? []).forEach((r) => r.source_id && ids.add(r.source_id));
        } else if (payload.card === 'change_set') {
          payload.data.changes.forEach((c) => {
            c.change.new_source_ids.forEach((s) => ids.add(s));
            c.diff.citations.anchors.forEach((a) => {
              a.source_ids_before.forEach((s) => ids.add(s));
              a.source_ids_after.forEach((s) => ids.add(s));
            });
          });
          fromChangeSet(payload.data.changes);
        }
      });
    });

    if (pending?.kind === 'commit_change_set') {
      pending.proposal.changes.forEach((c) => {
        c.change.new_source_ids.forEach((s) => ids.add(s));
        c.diff.citations.anchors.forEach((a) => {
          a.source_ids_before.forEach((s) => ids.add(s));
          a.source_ids_after.forEach((s) => ids.add(s));
        });
      });
      fromChangeSet(pending.proposal.changes);
    }

    return [...ids].sort();
  }, [transcript, pending]);
}

function useSources(ids: string[]): Record<string, SourceRecord> {
  const [sources, setSources] = useState<Record<string, SourceRecord>>({});
  const key = ids.join(',');

  useEffect(() => {
    const missing = key === '' ? [] : key.split(',').filter((id) => !(id in sources));
    if (missing.length === 0) return;

    let live = true;
    const client = getClient();
    void Promise.allSettled(missing.map((id) => client.getSource(id))).then((settled) => {
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
    // `sources` is read to skip records we already hold, but it must not
    // retrigger the effect — that is the loop that fetches for ever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return sources;
}
