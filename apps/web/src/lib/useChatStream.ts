'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getClient } from './api/client';
import { readToolPayload } from './api/chat-payloads';
import type {
  ChatConfirmation,
  ChatEvent,
  ChatHandle,
  ChatParseProgress,
  ChatReviewProgress,
  Conversation,
  JsonObject,
  ToolPayload,
} from './api/types';

/**
 * The conversation, assembled from the stream.
 *
 * Modelled on `useReviewStream`, and it holds the same two honesty properties
 * one level up: in-progress never reads as complete, and a dropped stream is
 * surfaced rather than left looking finished. Three more are specific to a
 * conversation:
 *
 *  - **A dropped delta cannot corrupt what is on screen.** Deltas append to the
 *    in-flight message; the final `message` *replaces* it. So the worst a lost
 *    delta can do is show a gap for a moment, never a mangled sentence that
 *    stays.
 *
 *  - **A tool that never returns looks unfinished.** A `tool_call` with no
 *    `tool_result` stays `in_flight` for ever rather than ageing into something
 *    that reads as done. The screen shows a half seal; the user can tell.
 *
 *  - **A refresh repaints and then follows, without doubling.** The cold load
 *    seeds the transcript from the message log and the stream then replays its
 *    whole event log on top. Everything is keyed — turns by `message_id`, tool
 *    calls by `call_id` — so the replay reconciles instead of appending. A turn
 *    already complete is never rewound to streaming, which is what stops a
 *    replayed `message_start` blanking a message that is already on screen.
 */

export type ChatPhase =
  | 'connecting'
  | 'idle'
  | 'thinking'
  | 'streaming'
  | 'awaiting_confirmation'
  | 'interrupted'
  | 'failed';

export type ToolState = 'in_flight' | 'ok' | 'failed';

export interface ToolCallState {
  call_id: string;
  name: string;
  /** The registry's own human phrase — "Reading the parse report". */
  label: string;
  arguments: JsonObject;
  state: ToolState;
  /** The tool's factual line. Present once it has returned. */
  summary: string | null;
  /** Shown in full when the tool failed. Never softened, never truncated. */
  error: string | null;
  payload: ToolPayload;
}

export interface ChatTurn {
  message_id: string;
  role: 'user' | 'assistant' | 'system_notice';
  content: string;
  /** False once the final `message` arrived. A complete turn never rewinds. */
  complete: boolean;
  toolCalls: ToolCallState[];
}

/**
 * The transcript is not only messages.
 *
 * A parse or a review that finished mid-conversation is part of the record of
 * what happened, and it belongs at the point it happened rather than only in a
 * card that disappears when the work ends. So a completed run settles into the
 * list, in order, and the live card above the composer is for the run still
 * going.
 */
export type TranscriptItem =
  | { kind: 'turn'; id: string; turn: ChatTurn }
  | { kind: 'parse_settled'; id: string; progress: ChatParseProgress }
  | { kind: 'review_settled'; id: string; progress: ChatReviewProgress };

/**
 * What went wrong, kept apart from `phase` on purpose.
 *
 * A turn's failure and the conversation's activity are two different facts, and
 * folding them into one field loses one of them: the watcher keeps emitting
 * progress and the runtime can open the next turn while a failure notice is
 * still the most important thing on screen. Deriving the notice from `phase`
 * meant the very next background event overwrote it — a Stop the user pressed
 * vanished within 300ms because an unrelated parse tick arrived behind it.
 *
 * So a notice is sticky. It clears when the stream recovers (for an
 * interruption) or when the user sends their next message, and not otherwise.
 */
export interface ChatNotice {
  kind: 'interrupted' | 'failed';
  /** The server's own words. Never replaced with a friendlier guess. */
  message: string;
  detail: string | null;
}

export interface ChatState {
  phase: ChatPhase;
  transcript: TranscriptItem[];
  /** The ingest, while it is running. Null once it has settled or if it never ran. */
  parse: ChatParseProgress | null;
  review: ChatReviewProgress | null;
  pending: ChatConfirmation | null;
  notice: ChatNotice | null;
  tokensUsed: number | null;
  budgetRemaining: number | null;
  /** Non-null when the conversation itself could not be reached. */
  fatal: string | null;
}

const EMPTY: ChatState = {
  phase: 'connecting',
  transcript: [],
  parse: null,
  review: null,
  pending: null,
  notice: null,
  tokensUsed: null,
  budgetRemaining: null,
  fatal: null,
};

/** Turns only, for the reconciliation helpers below. */
const turnAt = (items: TranscriptItem[], messageId: string) =>
  items.findIndex((i) => i.kind === 'turn' && i.turn.message_id === messageId);

const lastTurnIndex = (items: TranscriptItem[], role: ChatTurn['role']) => {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind === 'turn' && item.turn.role === role) return i;
  }
  return -1;
};

const replaceTurn = (
  items: TranscriptItem[],
  index: number,
  update: (turn: ChatTurn) => ChatTurn,
): TranscriptItem[] => {
  const item = items[index];
  if (item.kind !== 'turn') return items;
  const next = [...items];
  next[index] = { ...item, turn: update(item.turn) };
  return next;
};

export interface ChatController extends ChatState {
  send: (text: string) => void;
  stop: () => void;
  /** True while a turn is running, so the composer knows to disable itself. */
  busy: boolean;
}

export function useChatStream(conversation: Conversation | null): ChatController {
  const [state, setState] = useState<ChatState>(EMPTY);
  const handleRef = useRef<ChatHandle | null>(null);
  const convRef = useRef<Conversation | null>(null);

  convRef.current = conversation;

  const apply = useCallback((event: ChatEvent) => {
    setState((prev) => reduce(prev, event));
  }, []);

  useEffect(() => {
    if (!conversation) return;
    let live = true;
    const client = getClient();

    setState({ ...EMPTY, phase: 'connecting' });

    // Cold load first, then subscribe. The order matters: the log paints the
    // conversation immediately, and the stream's replay lands on top of it and
    // reconciles by id rather than appending a second copy of everything.
    client
      .getConversation(conversation.conversation_id)
      .then((log) => {
        if (!live) return;
        setState((prev) => ({
          ...prev,
          phase: 'idle',
          transcript: log.messages
            .filter(
              (m): m is typeof m & { role: ChatTurn['role'] } =>
                m.role === 'user' || m.role === 'assistant' || m.role === 'system_notice',
            )
            .map((m) => ({
              kind: 'turn' as const,
              id: m.message_id,
              turn: {
                message_id: m.message_id,
                role: m.role,
                content: m.content,
                // Everything in the log is finished by definition. Marking it so
                // is what stops a replayed delta appending to a message that is
                // already whole.
                complete: true,
                toolCalls: [],
              },
            })),
        }));
      })
      .catch((err: unknown) => {
        if (!live) return;
        setState((prev) => ({
          ...prev,
          phase: 'failed',
          fatal: err instanceof Error ? err.message : String(err),
        }));
      })
      .finally(() => {
        if (!live) return;
        handleRef.current = client.subscribeChat(conversation, apply);
      });

    return () => {
      live = false;
      handleRef.current?.close();
      handleRef.current = null;
    };
  }, [conversation, apply]);

  const send = useCallback((text: string) => {
    const conv = convRef.current;
    const trimmed = text.trim();
    if (!conv || trimmed === '') return;

    // Shown at once, under a local id, and adopted by the server's id when the
    // stream echoes it back. Waiting for the round trip would leave the user's
    // own sentence missing for as long as the API takes to accept it.
    const localId = `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    setState((prev) => ({
      ...prev,
      phase: 'thinking',
      // The proposal the user was answering is no longer pending — they just
      // answered it, whatever they said.
      pending: null,
      // A new message is the user moving on; the previous turn's notice has
      // been read or does not matter any more.
      notice: null,
      transcript: [
        ...prev.transcript,
        {
          kind: 'turn',
          id: localId,
          turn: {
            message_id: localId,
            role: 'user',
            content: trimmed,
            complete: true,
            toolCalls: [],
          },
        },
      ],
    }));

    getClient()
      .sendMessage(conv.conversation_id, trimmed)
      .catch((err: unknown) => {
        setState((prev) => ({
          ...prev,
          phase: 'failed',
          notice: {
            kind: 'failed',
            message: 'Your message did not reach the API.',
            detail: err instanceof Error ? err.message : String(err),
          },
        }));
      });
  }, []);

  const stop = useCallback(() => {
    const conv = convRef.current;
    if (!conv) return;
    getClient()
      .stopTurn(conv.conversation_id)
      .catch((err: unknown) => {
        setState((prev) => ({
          ...prev,
          notice: {
            kind: 'failed',
            message: 'The stop request did not reach the API.',
            detail: err instanceof Error ? err.message : String(err),
          },
        }));
      });
  }, []);

  const busy = state.phase === 'thinking' || state.phase === 'streaming';

  return useMemo(() => ({ ...state, send, stop, busy }), [state, send, stop, busy]);
}

/**
 * One event → the next state.
 *
 * Pure, and separate from the hook, because every honesty property above is a
 * property of this function and nothing else.
 */
function reduce(prev: ChatState, event: ChatEvent): ChatState {
  // Any event proves the stream is alive again, so an *interruption* clears —
  // the same recovery `useReviewStream` does, and for the same reason: a
  // "reconnecting…" notice that outlives the reconnection is a lie.
  //
  // A `failed` notice does not clear here. It is a statement about a turn that
  // ended, and the next background progress tick is not evidence against it.
  const recovered: ChatState =
    prev.notice?.kind === 'interrupted' ? { ...prev, phase: 'idle', notice: null } : prev;

  switch (event.type) {
    case 'heartbeat':
      return recovered;

    case 'message_start': {
      const { message_id, role } = event.data;
      const at = turnAt(recovered.transcript, message_id);
      if (at >= 0) {
        // Already known — a replay of a turn we have. Never rewind a complete
        // message to streaming; that would blank text already on screen and
        // re-type it from the deltas that follow.
        return { ...recovered, phase: 'streaming' };
      }
      return {
        ...recovered,
        phase: 'streaming',
        transcript: [
          ...recovered.transcript,
          {
            kind: 'turn',
            id: message_id,
            turn: {
              message_id,
              role: role === 'user' || role === 'system_notice' ? role : 'assistant',
              content: '',
              complete: false,
              toolCalls: [],
            },
          },
        ],
      };
    }

    case 'message_delta': {
      const at = turnAt(recovered.transcript, event.data.message_id);
      if (at < 0) return recovered;
      const item = recovered.transcript[at];
      if (item.kind !== 'turn' || item.turn.complete) return recovered;
      return {
        ...recovered,
        phase: 'streaming',
        transcript: replaceTurn(recovered.transcript, at, (turn) => ({
          ...turn,
          content: turn.content + event.data.text,
        })),
      };
    }

    case 'message': {
      const { message_id, role, content } = event.data;
      const at = turnAt(recovered.transcript, message_id);

      if (at >= 0) {
        // The authoritative text. It *replaces* the accumulated deltas rather
        // than being compared to them, so a delta that never arrived leaves no
        // trace once the turn closes.
        return {
          ...recovered,
          phase: 'thinking',
          transcript: replaceTurn(recovered.transcript, at, (turn) => ({
            ...turn,
            content,
            complete: true,
          })),
        };
      }

      if (role === 'user') {
        // The server's echo of a message we already showed optimistically.
        // Adopt its id rather than adding a second copy of the same sentence.
        const localAt = lastTurnIndex(recovered.transcript, 'user');
        const local = localAt >= 0 ? recovered.transcript[localAt] : null;
        if (
          local &&
          local.kind === 'turn' &&
          local.turn.message_id.startsWith('local-') &&
          local.turn.content === content
        ) {
          const next = [...recovered.transcript];
          next[localAt] = {
            kind: 'turn',
            id: message_id,
            turn: { ...local.turn, message_id },
          };
          return { ...recovered, transcript: next };
        }
      }

      return {
        ...recovered,
        phase: role === 'user' ? recovered.phase : 'thinking',
        transcript: [
          ...recovered.transcript,
          {
            kind: 'turn',
            id: message_id,
            turn: {
              message_id,
              role: role === 'user' || role === 'system_notice' ? role : 'assistant',
              content,
              complete: true,
              toolCalls: [],
            },
          },
        ],
      };
    }

    case 'tool_call': {
      const call: ToolCallState = {
        call_id: event.data.call_id,
        name: event.data.name,
        label: event.data.label,
        arguments: event.data.arguments,
        state: 'in_flight',
        summary: null,
        error: null,
        payload: { card: 'none' },
      };

      // Attached to the assistant turn that issued it. A model that called a
      // tool without saying anything first has no such turn yet, so one is
      // opened to hold the call — the work still has to be visible.
      const at = lastTurnIndex(recovered.transcript, 'assistant');
      const host = at >= 0 ? recovered.transcript[at] : null;

      if (host && host.kind === 'turn' && !hasCall(host.turn, call.call_id)) {
        return {
          ...recovered,
          phase: 'thinking',
          transcript: replaceTurn(recovered.transcript, at, (turn) => ({
            ...turn,
            toolCalls: [...turn.toolCalls, call],
          })),
        };
      }
      if (host && host.kind === 'turn' && hasCall(host.turn, call.call_id)) {
        return { ...recovered, phase: 'thinking' };
      }

      const holderId = `tools-${call.call_id}`;
      return {
        ...recovered,
        phase: 'thinking',
        transcript: [
          ...recovered.transcript,
          {
            kind: 'turn',
            id: holderId,
            turn: {
              message_id: holderId,
              role: 'assistant',
              content: '',
              complete: true,
              toolCalls: [call],
            },
          },
        ],
      };
    }

    case 'tool_result': {
      const { call_id, ok, summary, error } = event.data;
      const at = recovered.transcript.findIndex(
        (i) => i.kind === 'turn' && hasCall(i.turn, call_id),
      );
      if (at < 0) return recovered;

      return {
        ...recovered,
        transcript: replaceTurn(recovered.transcript, at, (turn) => ({
          ...turn,
          toolCalls: turn.toolCalls.map((c) =>
            c.call_id === call_id
              ? {
                  ...c,
                  state: ok ? ('ok' as const) : ('failed' as const),
                  summary,
                  // Either field carries the reason; one of them is always
                  // shown in full, because a failed tool with no stated cause
                  // is the failure HR-3 exists to prevent.
                  error: ok ? null : (error ?? summary ?? 'The tool failed and gave no reason.'),
                  // A failed tool gets no card, whatever is in `data`.
                  // `get_document_outline` refusing with "the ingest is at
                  // stage 'grobid', before the PDF has been turned into a
                  // document" carries `data: {}` — and an outline card built
                  // from that would be an empty document presented as the
                  // paper. The reason is the answer, and the line above shows
                  // it in full.
                  payload: ok ? readToolPayload(c.name, event.data.data) : { card: 'none' },
                }
              : c,
          ),
        })),
      };
    }

    case 'progress': {
      if (event.data.kind === 'review') {
        const finished =
          event.data.total > 0 && event.data.verified >= event.data.total;
        return {
          ...recovered,
          review: finished ? null : event.data,
          transcript: finished
            ? settle(recovered.transcript, {
                kind: 'review_settled',
                id: 'review-settled',
                progress: event.data,
              })
            : recovered.transcript,
        };
      }

      const finished = event.data.state === 'complete' || event.data.state === 'failed';
      return {
        ...recovered,
        parse: finished ? null : event.data,
        transcript: finished
          ? settle(recovered.transcript, {
              kind: 'parse_settled',
              id: 'parse-settled',
              progress: event.data,
            })
          : recovered.transcript,
      };
    }

    case 'awaiting_confirmation':
      return { ...recovered, pending: event.data };

    case 'done':
      return {
        ...recovered,
        // The turn ended. If it ended on a question, saying so is a different
        // state from idle, and the screen renders it differently.
        phase: recovered.pending ? 'awaiting_confirmation' : 'idle',
        transcript: recovered.transcript.map((item) =>
          item.kind === 'turn' && !item.turn.complete
            ? { ...item, turn: { ...item.turn, complete: true } }
            : item,
        ),
        tokensUsed: event.data.tokens_used ?? recovered.tokensUsed,
        budgetRemaining: event.data.budget_remaining ?? recovered.budgetRemaining,
      };

    case 'error':
      return {
        ...prev,
        // A named failure is terminal for the *turn*; the transcript stays and
        // the composer re-enables. A transport drop may reconnect and says so.
        phase: event.data.recoverable ? 'interrupted' : 'failed',
        notice: {
          kind: event.data.recoverable ? 'interrupted' : 'failed',
          message: event.data.message,
          detail: event.data.detail,
        },
        // An in-flight message stops being in flight. Leaving it streaming
        // would keep the composer disabled behind a turn that is not coming
        // back — the transcript must stay usable.
        transcript: prev.transcript.map((item) =>
          item.kind === 'turn' && !item.turn.complete
            ? { ...item, turn: { ...item.turn, complete: true } }
            : item,
        ),
      };
  }
}

const hasCall = (turn: ChatTurn, callId: string) =>
  turn.toolCalls.some((c) => c.call_id === callId);

/** Idempotent: a replayed completion updates the marker rather than adding one. */
function settle(items: TranscriptItem[], marker: TranscriptItem): TranscriptItem[] {
  const at = items.findIndex((i) => i.id === marker.id);
  if (at < 0) return [...items, marker];
  const next = [...items];
  next[at] = marker;
  return next;
}
