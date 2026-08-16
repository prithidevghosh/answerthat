/**
 * A scripted stand-in for the orchestrator, for `NEXT_PUBLIC_USE_FIXTURES=1`.
 *
 * The fixture client has to answer the same five methods as the live one or the
 * seam is not a seam — an optional method would typecheck and then break in the
 * browser, which is the one failure mode the interface exists to make
 * impossible.
 *
 * **This file routes on keywords, and that is only allowed here.** In the real
 * system routing is the model's job, expressed as a tool call; a keyword match
 * in a component or a hook would make the flow deterministic with extra steps.
 * This module is standing in for the *server*, so it is allowed to fake the
 * server's decisions — and it is the only place in the frontend that may.
 *
 * The timings imitate the real system rather than flattering it. Text arrives in
 * deltas because a clumped stream is the most likely thing to be wrong in the
 * live path and the UI has to be built against progressive text. The parse takes
 * long enough that a question can be asked while it runs. A tool call sits
 * in-flight for a beat before its result, so an unfinished tool has a state to
 * render.
 *
 * The event log is mirrored into `sessionStorage` so a refresh replays it, the
 * way the server replays `chat_events`. Without that, the cold-load
 * reconciliation in `useChatStream` — the thing that stops a refresh doubling
 * the transcript — has nothing to be tested against in fixture mode.
 */
import { readConfirmation } from './chat-payloads';
import type {
  ChangeSetProposal,
  ChatEvent,
  ChatHandle,
  ChatLogMessage,
  Conversation,
  ConversationLog,
  JsonObject,
} from './types';
import * as F from './fixtures';

interface Turn {
  cancelled: boolean;
}

interface Live {
  conv: Conversation;
  docId: string;
  /** The replay log. Heartbeats are not in it — they carry nothing to replay. */
  events: ChatEvent[];
  messages: ChatLogMessage[];
  listeners: Set<(e: ChatEvent) => void>;
  /** Resolvers for the sleeps a turn is sitting in, so Stop can flush them. */
  pending: Set<() => void>;
  seq: number;
  parseComplete: boolean;
  planDescribed: boolean;
  reviewRun: boolean;
  proposal: ChangeSetProposal | null;
  exportOffered: boolean;
  turn: Turn | null;
  /**
   * A Stop that arrived before the turn it meant to stop.
   *
   * `POST /messages` answers 202 and the turn starts behind it, so there is a
   * window where the composer is already disabled and the Stop button is
   * already on screen but no turn exists yet to cancel. A Stop pressed in that
   * window used to find nothing and return quietly — leaving the user with a
   * disabled composer and a button that did nothing, which is the exact trap
   * the control exists to prevent. It is latched instead, and the turn is born
   * cancelled.
   */
  stopRequested: boolean;
  started: boolean;
}

const conversations = new Map<string, Live>();

const KEY = (id: string) => `answerthat.fixture.chat.${id}`;

interface Persisted {
  events: ChatEvent[];
  messages: ChatLogMessage[];
  seq: number;
  parseComplete: boolean;
  planDescribed: boolean;
  reviewRun: boolean;
}

function save(live: Live) {
  if (typeof sessionStorage === 'undefined') return;
  try {
    sessionStorage.setItem(
      KEY(live.conv.conversation_id),
      JSON.stringify({
        events: live.events,
        messages: live.messages,
        seq: live.seq,
        parseComplete: live.parseComplete,
        planDescribed: live.planDescribed,
        reviewRun: live.reviewRun,
      }),
    );
  } catch {
    // A full or unavailable sessionStorage is not worth failing a fixture over.
  }
}

function restore(live: Live) {
  if (typeof sessionStorage === 'undefined') return;
  try {
    const raw = sessionStorage.getItem(KEY(live.conv.conversation_id));
    if (!raw) return;
    const parsed = JSON.parse(raw) as Partial<Persisted>;
    live.events = parsed.events ?? [];
    live.messages = parsed.messages ?? [];
    live.seq = parsed.seq ?? 0;
    live.parseComplete = parsed.parseComplete ?? false;
    live.planDescribed = parsed.planDescribed ?? false;
    live.reviewRun = parsed.reviewRun ?? false;
    live.started = live.events.length > 0;
  } catch {
    // A log we cannot parse is a log we do not have.
  }
}

// --- the machinery ---

function emit(live: Live, event: ChatEvent) {
  if (event.type !== 'heartbeat') {
    live.events.push(event);
    save(live);
  }
  live.listeners.forEach((fn) => fn(event));
}

/**
 * A sleep the Stop control can cut short.
 *
 * The resolver is registered so `stopTurn` can fire it immediately rather than
 * cancelling the timer — a cleared timer would leave the turn's promise pending
 * for ever, which is a Stop button that silently does nothing. It resolves, then
 * throws, and the turn unwinds at its next await.
 */
function sleep(live: Live, ms: number, turn: Turn): Promise<void> {
  return new Promise<void>((resolve) => {
    const fire = () => {
      live.pending.delete(fire);
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(fire, ms);
    live.pending.add(fire);
  }).then(() => {
    if (turn.cancelled) throw new StopRequested();
  });
}

class StopRequested extends Error {}

let nextId = 0;
const id = (prefix: string) => `${prefix}-fx-${Date.now().toString(36)}-${++nextId}`;

function record(live: Live, role: ChatLogMessage['role'], messageId: string, content: string) {
  live.messages.push({
    message_id: messageId,
    seq: ++live.seq,
    role,
    content,
    tool_calls: null,
    tool_call_id: null,
    created_at: new Date().toISOString(),
  });
  save(live);
}

/**
 * One assistant message, streamed.
 *
 * Chunked at a few words rather than per character: the real stream sends
 * whatever the model's tokeniser produced, and building the UI against a smooth
 * per-character feed would hide the ragged arrival it actually has to survive.
 */
async function say(live: Live, turn: Turn, text: string) {
  const messageId = id('msg');
  emit(live, { type: 'message_start', data: { message_id: messageId, role: 'assistant' } });

  const words = text.split(' ');
  for (let i = 0; i < words.length; i += 3) {
    await sleep(live, 55, turn);
    emit(live, {
      type: 'message_delta',
      data: { message_id: messageId, text: (i === 0 ? '' : ' ') + words.slice(i, i + 3).join(' ') },
    });
  }

  await sleep(live, 80, turn);
  emit(live, {
    type: 'message',
    data: { message_id: messageId, role: 'assistant', content: text },
  });
  record(live, 'assistant', messageId, text);
  return messageId;
}

async function tool(
  live: Live,
  turn: Turn,
  spec: {
    name: string;
    label: string;
    args?: JsonObject;
    ok?: boolean;
    summary: string;
    data?: JsonObject | null;
    error?: string;
    latencyMs?: number;
  },
) {
  const callId = id('call');
  emit(live, {
    type: 'tool_call',
    data: { call_id: callId, name: spec.name, arguments: spec.args ?? {}, label: spec.label },
  });
  // A beat with the call in flight, so an unfinished tool has a state on screen.
  await sleep(live, spec.latencyMs ?? 700, turn);
  emit(live, {
    type: 'tool_result',
    data: {
      call_id: callId,
      name: spec.name,
      ok: spec.ok ?? true,
      summary: spec.summary,
      data: spec.data ?? null,
      error: spec.error ?? null,
    },
  });
}

const done = (live: Live, tokens: number) =>
  emit(live, {
    type: 'done',
    data: { message_id: null, tokens_used: tokens, budget_remaining: 400_000 - tokens },
  });

/** Runs a turn, swallowing only the cancellation `stopTurn` raises. */
function runTurn(live: Live, body: (turn: Turn) => Promise<void>) {
  // Born cancelled if a Stop landed in the gap between the 202 and this turn.
  const turn: Turn = { cancelled: live.stopRequested };
  live.stopRequested = false;
  live.turn = turn;
  void body(turn)
    .catch((err: unknown) => {
      if (err instanceof StopRequested) {
        emit(live, {
          type: 'error',
          data: {
            message: 'You stopped this turn.',
            detail: 'Nothing was written. The conversation is still usable.',
            recoverable: false,
          },
        });
        return;
      }
      throw err;
    })
    .finally(() => {
      if (live.turn === turn) live.turn = null;
    });
}

// --- the script ---

const json = (v: unknown) => v as unknown as JsonObject;

/** The ingest, narrated. Runs once per conversation, on the first subscribe. */
function runParse(live: Live) {
  runTurn(live, async (turn) => {
    const stages: [string, number][] = [
      ['grobid', 0.14],
      ['tei_to_ir', 0.28],
      ['references', 0.45],
      ['repair', 0.6],
      ['arbiter', 0.76],
      ['style', 0.88],
      ['persist', 0.96],
    ];

    await say(
      live,
      turn,
      'I have your paper and GROBID is reading it now. While that runs I can already answer questions about the text — but the bibliography is not reconciled yet, so I will not have final reference counts for a minute or so.',
    );
    done(live, 180);

    for (const [stage, fraction] of stages) {
      await sleep(live, 2400, turn);
      emit(live, {
        type: 'progress',
        data: {
          kind: 'parse',
          state: 'running',
          stage,
          fraction,
          filename: 'sparse-attention-routing.pdf',
          error: null,
        },
      });
    }

    await sleep(live, 1800, turn);
    emit(live, {
      type: 'progress',
      data: {
        kind: 'parse',
        state: 'complete',
        stage: 'complete',
        fraction: 1,
        filename: 'sparse-attention-routing.pdf',
        error: null,
      },
    });
    live.parseComplete = true;

    // The watcher appends a system notice and runs a turn; the model writes the
    // sentence. Nothing about this message is a template — in the real system it
    // is composed from the tool result the agent just read.
    await tool(live, turn, {
      name: 'get_parse_report',
      label: 'Reading the parse report',
      args: { doc_id: live.docId, include: 'counts' },
      summary: `${F.COUNTS.total_detected} references detected: ${F.COUNTS.resolved} resolved, ${F.COUNTS.parsed_unresolved} parsed but unresolved, ${F.COUNTS.low_confidence} low confidence, ${F.COUNTS.quarantined} quarantined. ${F.COUNTS.orphan_marker} orphan marker.`,
      data: json({ doc_id: live.docId, counts: F.COUNTS, style_id: 'ieee' }),
    });

    await say(
      live,
      turn,
      `Parsing is finished. Of ${F.COUNTS.total_detected} references detected, ${F.COUNTS.resolved} resolved against a real index, ${F.COUNTS.parsed_unresolved} parsed but matched nothing, ${F.COUNTS.low_confidence} came back with uncertain fields and ${F.COUNTS.quarantined} could not be parsed at all. There is also ${F.COUNTS.orphan_marker} in-text marker citing an entry that is not in your bibliography. I can show you the full reference list, or run a review — say which you would like.`,
    );
    done(live, 940);
  });
}

const REVIEW_PLAN = json({
  strategies: ['crossref_query', 'openalex_search', 'openalex_citation_graph'],
  all_strategies: [
    'crossref_query',
    'openalex_search',
    'openalex_citation_graph',
    'semantic_scholar_search',
  ],
  rerank_keep: 12,
  verify_keep: 5,
  citability_min: 0.35,
  estimated_claims: F.REVIEW_TOTAL,
  estimated_duration_s: 280,
  notes: [
    'SEMANTIC_SCHOLAR_API_KEY is not set, so the Semantic Scholar strategy will not run and candidate recall will be lower.',
    'Every finding is quote-checked against the fetched abstract; a candidate whose abstract cannot be fetched is reported as unverifiable rather than as supporting.',
  ],
});

async function describePlan(live: Live, turn: Turn) {
  await tool(live, turn, {
    name: 'describe_review_plan',
    label: 'Checking what a review would actually do',
    args: { doc_id: live.docId },
    summary:
      '3 of 4 retrieval strategies available (Semantic Scholar key absent). rerank_keep=12, verify_keep=5, citability_min=0.35. ~47 claims, roughly 5 minutes at the provider rate limit.',
    data: REVIEW_PLAN,
    latencyMs: 900,
  });

  await say(
    live,
    turn,
    'Here is what a review would do on this paper. I extract every citable claim — about 47 here — and for each one search Crossref and OpenAlex, including a one-hop walk of the citation graph. Semantic Scholar is not configured on this instance, so three of the four retrieval strategies will run rather than four; that means lower recall, not thinner results dressed up as a clean bill of health. The top 12 candidates per claim are reranked, the top 5 verified, and every finding is quote-checked against the fetched abstract — if I cannot fetch an abstract I report the claim as unverifiable instead of as supported. At roughly one request a second it takes about five minutes. Shall I run it?',
  );
  live.planDescribed = true;
  done(live, 1_450);
}

async function runReview(live: Live, turn: Turn) {
  await tool(live, turn, {
    name: 'start_review',
    label: 'Starting the review',
    args: { doc_id: live.docId },
    summary: 'Review job job-fixture-1 started for 47 claims.',
    data: json({ job_id: 'job-fixture-1', doc_id: live.docId }),
    latencyMs: 500,
  });

  await say(
    live,
    turn,
    'Running. I will report progress as it goes — findings arrive most-citable first, so the early ones are the ones that matter most, and the list is not final until it finishes.',
  );
  done(live, 320);

  const total = F.REVIEW_TOTAL;
  for (let verified = 4; verified <= total; verified += 6) {
    await sleep(live, 1500, turn);
    emit(live, {
      type: 'progress',
      data: {
        kind: 'review',
        verified: Math.min(verified, total),
        total,
        findings_emitted: Math.min(F.FINDINGS.length, Math.floor(verified / 12)),
        candidates_considered: verified * 11,
        quote_check_failures: Math.floor(verified / 4),
        unverifiable_no_abstract: Math.floor(verified / 8),
        claims_without_candidates: Math.floor(verified / 16),
        error: null,
      },
    });
  }

  await sleep(live, 900, turn);
  emit(live, {
    type: 'progress',
    data: {
      kind: 'review',
      verified: total,
      total,
      findings_emitted: F.FINDINGS.length,
      candidates_considered: 512,
      quote_check_failures: 11,
      unverifiable_no_abstract: 6,
      claims_without_candidates: 3,
      error: null,
    },
  });

  await tool(live, turn, {
    name: 'list_findings',
    label: 'Reading the findings',
    args: { doc_id: live.docId, limit: 20, offset: 0 },
    summary: `${F.FINDINGS.length} findings from ${total} claims verified. 11 candidates discarded on the quote check, 6 abstracts unavailable, 3 claims produced no candidates at all.`,
    data: json({ findings: F.FINDINGS, total: F.FINDINGS.length }),
  });

  live.reviewRun = true;
  await say(
    live,
    turn,
    `The review finished: all ${total} claims verified, ${F.FINDINGS.length} findings. That is a short list and it is worth saying why — 11 candidates were discarded because the quote I would have shown you was not verbatim in the abstract, 6 abstracts could not be fetched at all so those claims are unverifiable rather than clean, and 3 claims returned no candidates. The findings are above, most citable first. Ask me about any of them, or tell me what you want changed.`,
  );
  done(live, 2_100);
}

async function proposeEdit(live: Live, turn: Turn, instruction: string) {
  await say(
    live,
    turn,
    'Planning that as typed operations now. Nothing is written until you approve it.',
  );

  await tool(live, turn, {
    name: 'propose_edit',
    label: 'Planning the edit',
    args: { doc_id: live.docId, instruction },
    summary:
      '2 changes accepted with warnings, 1 operation refused by the kernel. 1 citation anchor could not be reattached and needs your decision.',
    data: json({
      change_set_id: F.COMMAND_RESULT.change_set_id,
      doc_id: F.COMMAND_RESULT.doc_id,
      base_version: F.COMMAND_RESULT.base_version,
      changes: F.COMMAND_RESULT.changes,
      rejected: F.COMMAND_RESULT.rejected,
      message: F.COMMAND_RESULT.message,
    }),
    latencyMs: 1_600,
  });

  const proposal: ChangeSetProposal = {
    change_set_id: F.COMMAND_RESULT.change_set_id,
    doc_id: F.COMMAND_RESULT.doc_id,
    base_version: F.COMMAND_RESULT.base_version,
    changes: F.COMMAND_RESULT.changes,
    rejected: F.COMMAND_RESULT.rejected,
    message: F.COMMAND_RESULT.message,
  };
  live.proposal = proposal;

  // Through `readConfirmation`, and under the runtime's *own* name for it
  // (`change_set`, not `commit_change_set`). A fixture that emitted the already
  // normalised shape would skip the boundary the live path depends on, and the
  // vocabulary mismatch that disabled the orphan gate would not have shown up
  // here at all — which is the whole reason to keep the two clients honest.
  emit(live, {
    type: 'awaiting_confirmation',
    data: readConfirmation(json({ kind: 'change_set', proposal })),
  });

  await say(
    live,
    turn,
    'Two changes survived the kernel and one operation was refused — the rewrite would have shrunk the citation set by two without an approved removal, and the kernel will not allow that. The diffs and the citation ledger are above. One thing I cannot decide for you: shortening that sentence left the anchor for [3] with nowhere to sit. The closest sentence scored 0.68 against the 0.82 I need before moving a citation on your behalf, so it is yours to place — keep it where it is, move it to that sentence, or remove it. Tell me which, and I will commit.',
  );
  done(live, 1_800);
}

async function commit(live: Live, turn: Turn) {
  const proposal = live.proposal;
  if (!proposal) {
    await say(
      live,
      turn,
      'There is no change set waiting, so there is nothing for me to commit. Tell me what you would like changed and I will plan it first.',
    );
    done(live, 120);
    return;
  }

  await tool(live, turn, {
    name: 'commit_change_set',
    label: 'Committing the approved changes',
    args: { change_set_id: proposal.change_set_id },
    summary: `Committed 2 changes as version ${proposal.base_version + 1}.`,
    data: json({
      committed: true,
      doc_id: proposal.doc_id,
      base_version: proposal.base_version,
      new_version: proposal.base_version + 1,
      applied_change_ids: ['ch-1', 'ch-2'],
      skipped: {},
      diff: null,
      verdict: null,
      message: `Committed 2 changes as version ${proposal.base_version + 1}. The decision you gave for anchor a-3 was applied with them.`,
    }),
    latencyMs: 1_100,
  });

  live.proposal = null;
  await say(
    live,
    turn,
    `Written as version ${proposal.base_version + 1}, with your anchor decision applied alongside the changes. The refused operation was not part of it — your paper still says what it said about routing. You can export from here, or keep editing.`,
  );
  done(live, 700);
}

async function offerExport(live: Live, turn: Turn) {
  await tool(live, turn, {
    name: 'get_export_manifest',
    label: 'Reading the export manifest',
    args: { doc_id: live.docId },
    summary:
      '1 figure, 1 table and 1 equation are placeholders. 38 bibliography entries, IEEE style, exportable.',
    data: json(F.EXPORT_MANIFEST),
    latencyMs: 800,
  });

  emit(live, {
    type: 'awaiting_confirmation',
    data: readConfirmation(json({ kind: 'export', proposal: F.EXPORT_MANIFEST })),
  });

  live.exportOffered = true;
  await say(
    live,
    turn,
    'Before you download it, one disclosure: figures, tables and equations are not carried through — there is one of each in this paper and each becomes a placeholder in the .tex, captions kept, content not. Everything else is real: 38 bibliography entries, rendered in IEEE. Shall I render it?',
  );
  done(live, 900);
}

async function answerDuringParse(live: Live, turn: Turn, text: string) {
  await tool(live, turn, {
    name: 'get_document_outline',
    label: 'Reading the document as extracted so far',
    args: { doc_id: live.docId },
    summary:
      'Draft IR available: 6 sections, 41 blocks, 96 spans. References are not reconciled yet.',
    data: json({
      doc_id: live.docId,
      title: F.DOCUMENT.metadata.title,
      version: null,
      sections: F.DOCUMENT.sections,
      block_count: 41,
      span_count: 96,
      is_draft: true,
    }),
    latencyMs: 700,
  });

  await say(
    live,
    turn,
    `I can answer that from the text, with one caveat: this is the document as GROBID extracted it, before references were reconciled, so any reference count I gave you now would be wrong. On the text itself — ${text.trim().replace(/[?.!]+$/, '')} — the paper is organised into six sections, and the argument you are asking about sits in the related work and the discussion. Ask me again once parsing finishes and I can tie it to the bibliography.`,
  );
  done(live, 800);
}

async function generalAnswer(live: Live, turn: Turn) {
  await tool(live, turn, {
    name: 'search_evidence',
    label: 'Searching the evidence index',
    args: { doc_id: live.docId, k: 8 },
    summary: '4 passages matched. The index finished building for this document.',
    data: json({
      results: F.DOCUMENT.sections.slice(0, 2).flatMap((section) =>
        section.blocks
          .flatMap((block) => block.spans)
          .slice(0, 2)
          .map((span, i) => ({
            kind: 'span',
            ref_id: span.id,
            text: span.text,
            score: 0.81 - i * 0.06,
          })),
      ),
      index_status: 'complete',
    }),
    latencyMs: 850,
  });

  await say(
    live,
    turn,
    'That is grounded in the passages above rather than in anything I remember about the field — each one is a real span from your paper, with its id, so you can check me. Ask me to open any of them in full, or tell me what you want changed.',
  );
  done(live, 620);
}

/**
 * The routing the model does in the real system.
 *
 * Keyword matching, and honestly labelled as the fake it is. The point of the
 * fixture is that the *screen* never routes — every branch below produces the
 * same events the live stream would, and the UI cannot tell which produced them.
 */
function respond(live: Live, text: string) {
  const t = text.toLowerCase();
  const affirmative = /\b(yes|yeah|go ahead|do it|run it|please do|commit|approve|apply|render)\b/.test(t);

  runTurn(live, async (turn) => {
    // The outstanding question is answered first. An agent that took "yes" as a
    // fresh topic every time would be unusable, and in the real system the
    // pending proposal is in the conversation the model is reading.
    if (affirmative && live.exportOffered) {
      await tool(live, turn, {
        name: 'export_latex',
        label: 'Rendering the LaTeX',
        args: { doc_id: live.docId },
        summary: 'Rendered sparse-attention-routing.revised.tex — 214 KB, IEEE.',
        data: json({
          filename: F.EXPORT_MANIFEST.filename,
          byte_size: 214_218,
          download_url: '#fixture-export',
          style_id: 'ieee',
          style_uncertain: false,
        }),
        latencyMs: 1_200,
      });
      live.exportOffered = false;
      await say(
        live,
        turn,
        'Rendered. The placeholders I described are in it as captions with no content; everything else is your paper as committed.',
      );
      done(live, 480);
      return;
    }

    if (affirmative && live.proposal) {
      await commit(live, turn);
      return;
    }

    if (affirmative && live.planDescribed && !live.reviewRun) {
      await runReview(live, turn);
      return;
    }

    if (/export|download|\.tex|latex/.test(t)) {
      await offerExport(live, turn);
      return;
    }

    if (/shorten|rewrite|edit|citation|tighten|cut|trim/.test(t)) {
      await proposeEdit(live, turn, text);
      return;
    }

    if (/review/.test(t)) {
      if (!live.planDescribed) {
        await describePlan(live, turn);
      } else if (!live.reviewRun) {
        await runReview(live, turn);
      } else {
        await say(
          live,
          turn,
          `The review has already run on this version — all ${F.REVIEW_TOTAL} claims, ${F.FINDINGS.length} findings. I will not bill a second pass over the same scope. If you want a section re-reviewed after an edit, say which section.`,
        );
        done(live, 260);
      }
      return;
    }

    if (!live.parseComplete) {
      await answerDuringParse(live, turn, text);
      return;
    }

    await generalAnswer(live, turn);
  });
}

// --- the five methods ---

export const fixtureChat = {
  async startConversation(docId: string): Promise<Conversation> {
    await new Promise((r) => setTimeout(r, 140));
    const conversationId = `conv-fixture-${docId}`;

    const existing = conversations.get(conversationId);
    if (existing) return existing.conv;

    const conv: Conversation = {
      conversation_id: conversationId,
      doc_id: docId,
      stream: `/api/chat/${conversationId}/stream`,
      poll: `/api/chat/${conversationId}`,
    };
    const live: Live = {
      conv,
      docId,
      events: [],
      messages: [],
      listeners: new Set(),
      pending: new Set(),
      seq: 0,
      parseComplete: false,
      planDescribed: false,
      reviewRun: false,
      proposal: null,
      exportOffered: false,
      turn: null,
      stopRequested: false,
      started: false,
    };
    restore(live);
    conversations.set(conversationId, live);
    return conv;
  },

  async getConversation(conversationId: string): Promise<ConversationLog> {
    await new Promise((r) => setTimeout(r, 90));
    const live = conversations.get(conversationId);
    if (!live) throw new Error(`No conversation ${conversationId}`);
    return {
      conversation_id: conversationId,
      doc_id: live.docId,
      status: live.turn ? 'running' : 'idle',
      messages: [...live.messages],
    };
  },

  async sendMessage(conversationId: string, text: string): Promise<{ accepted: true }> {
    const live = conversations.get(conversationId);
    if (!live) throw new Error(`No conversation ${conversationId}`);

    const messageId = id('msg');
    emit(live, { type: 'message', data: { message_id: messageId, role: 'user', content: text } });
    record(live, 'user', messageId, text);

    await new Promise((r) => setTimeout(r, 120));
    respond(live, text);
    return { accepted: true };
  },

  subscribeChat(conv: Conversation, onEvent: (e: ChatEvent) => void): ChatHandle {
    const live = conversations.get(conv.conversation_id);
    if (!live) {
      onEvent({
        type: 'error',
        data: {
          message: 'No such conversation.',
          detail: 'The fixture client has no conversation with that id.',
          recoverable: false,
        },
      });
      return { close() {} };
    }

    let closed = false;
    const listener = (e: ChatEvent) => {
      if (!closed) onEvent(e);
    };

    // Replay first, then follow live — the property `useChatStream` reconciles
    // against. Synchronously, so nothing emitted between the two is missed.
    live.events.forEach(listener);
    live.listeners.add(listener);

    const beat = setInterval(() => listener({ type: 'heartbeat' }), 15_000);

    if (!live.started) {
      live.started = true;
      runParse(live);
    }

    return {
      close() {
        closed = true;
        clearInterval(beat);
        live.listeners.delete(listener);
      },
    };
  },

  async stopTurn(conversationId: string): Promise<void> {
    const live = conversations.get(conversationId);
    if (!live) return;
    if (live.turn) live.turn.cancelled = true;
    else live.stopRequested = true;
    // Fire the sleeps rather than clearing their timers: a cleared timer leaves
    // the turn's promise pending for ever, which is a Stop that does nothing.
    [...live.pending].forEach((fire) => fire());
  },
};
