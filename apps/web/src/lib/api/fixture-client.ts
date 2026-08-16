import type { ApiClient } from './client';
import type { ApprovalPayload, ReviewEvent, ReviewHandle } from './types';
import { fixtureChat } from './fixture-chat';
import * as F from './fixtures';

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * Fixture client — development only. See ./fixtures for why it exists and why
 * it is loud about being fake.
 *
 * Timings imitate the real system rather than flattering it: a review at ~1 rps
 * on Semantic Scholar takes minutes (ADR-014), so findings here arrive spaced
 * out and out of step with the progress counter, which is what the streaming UI
 * actually has to survive.
 */
export const fixtureClient: ApiClient = {
  async getStatus() {
    return { kind: 'ok', missing_keys: [], detail: null };
  },

  // Split exactly as the live client is, so the fork after upload exercises the
  // same two calls in the same order. A fixture that did both halves in one
  // would let the conversational path look instant here and 404 in production.
  async uploadPdf(file, onProgress) {
    for (const f of [0.15, 0.42, 0.71, 1]) {
      await wait(180);
      onProgress({ stage: 'uploading', fraction: f, detail: file.name });
    }
    await wait(400);
    onProgress({ stage: 'extracting', fraction: null, detail: 'GROBID is reading the document' });
    return { doc_id: F.DOCUMENT.doc_id, job_id: 'job-fixture-1', version: null };
  },

  async waitForParse(docId, onProgress) {
    await wait(900);
    onProgress({ stage: 'parsing', fraction: 0.45, detail: 'Segmenting 47 references' });
    await wait(800);
    onProgress({
      stage: 'resolving',
      fraction: 0.78,
      detail: 'Reconciling against Crossref, Semantic Scholar, OpenAlex',
    });
    await wait(900);
    onProgress({ stage: 'complete', fraction: 1, detail: 'Ready' });
    return { doc_id: docId, job_id: 'job-fixture-1', version: F.DOCUMENT.version };
  },

  async getParseResult() {
    await wait(220);
    return F.PARSE_RESULT;
  },

  async getDocument() {
    await wait(120);
    return F.DOCUMENT;
  },

  async getParseStatus() {
    await wait(60);
    // A fixture document is always already parsed — `uploadPdf` above walks the
    // stages and does not resolve until it has.
    return {
      state: 'complete' as const,
      stage: 'complete',
      progress: 1,
      version: F.DOCUMENT.version,
      error: null,
    };
  },

  async chooseStyle() {
    await wait(120);
  },

  async getSource(sourceId) {
    await wait(80);
    const rec = F.SOURCES[sourceId];
    if (!rec) throw new Error(`No source record for ${sourceId}`);
    return rec;
  },

  async startReview(docId) {
    await wait(150);
    // Shaped like the real 202, stream URL included: a fixture that answered with
    // less than the API does is a fixture the live path can drift away from
    // unnoticed — which is precisely how the stream URL went wrong.
    return {
      job_id: 'job-fixture-1',
      doc_id: docId,
      stream: `/api/documents/${docId}/review/stream`,
      poll: `/api/documents/${docId}/review/status`,
    };
  },

  subscribeReview(_started, onEvent): ReviewHandle {
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const at = (ms: number, fn: () => void) => {
      timers.push(setTimeout(() => !cancelled && fn(), ms));
    };

    const total = F.REVIEW_TOTAL;
    let verified = 0;

    // Progress ticks independently of findings: most claims verify without
    // producing a finding, and the counter must keep saying so.
    for (let i = 1; i <= total; i++) {
      at(300 + i * 260, () => {
        verified = i;
        onEvent({ type: 'progress', data: { verified, total } });
      });
    }

    F.FINDINGS.forEach((finding, i) => {
      at(900 + i * 1500, () => onEvent({ type: 'finding', data: finding }));
    });

    // A mid-stream interruption, because the real one will happen and the UI
    // has to show it without looking finished.
    at(5200, () =>
      onEvent({
        type: 'error',
        data: { message: 'The review stream was interrupted. Reconnecting…', recoverable: true },
      }),
    );

    at(300 + total * 260 + 400, () => onEvent({ type: 'done', data: { verified: total, total } }));

    return {
      close() {
        cancelled = true;
        timers.forEach(clearTimeout);
      },
    };
  },

  async sendCommand() {
    await wait(1100);
    return F.COMMAND_RESULT;
  },

  async approveChangeSet(_changeSetId: string, payload: ApprovalPayload) {
    await wait(400);
    return {
      committed: payload.approved_change_ids.length > 0,
      doc_id: F.DOCUMENT.doc_id,
      base_version: payload.base_version,
      new_version:
        payload.approved_change_ids.length > 0 ? payload.base_version + 1 : null,
      applied_change_ids: payload.approved_change_ids,
      skipped: {},
      diff: null,
      verdict: null,
      message:
        payload.approved_change_ids.length > 0
          ? `Committed ${payload.approved_change_ids.length} change(s) as version ${payload.base_version + 1}.`
          : 'Nothing was approved, so nothing was written.',
    };
  },

  async getExportManifest() {
    await wait(200);
    return F.EXPORT_MANIFEST;
  },

  exportUrl() {
    return '#fixture-export';
  },

  // The conversation lives in ./fixture-chat — a scripted stand-in for the
  // orchestrator, kept out of here because it is a hundred times the size of
  // every other fixture method and is the only one that keeps state.
  startConversation: fixtureChat.startConversation,
  getConversation: fixtureChat.getConversation,
  sendMessage: fixtureChat.sendMessage,
  subscribeChat: fixtureChat.subscribeChat,
  stopTurn: fixtureChat.stopTurn,
};
