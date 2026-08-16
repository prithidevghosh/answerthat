import type { ApiClient } from './client';
import type { ReviewEvent, ReviewHandle } from './types';
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

  async uploadPdf(file, onProgress) {
    for (const f of [0.15, 0.42, 0.71, 1]) {
      await wait(180);
      onProgress({ stage: 'uploading', fraction: f, detail: file.name });
    }
    await wait(400);
    onProgress({ stage: 'extracting', fraction: null, detail: 'GROBID is reading the document' });
    await wait(900);
    onProgress({ stage: 'parsing', fraction: null, detail: 'Segmenting 47 references' });
    await wait(800);
    onProgress({ stage: 'resolving', fraction: null, detail: 'Reconciling against Crossref, Semantic Scholar, OpenAlex' });
    await wait(900);
    onProgress({ stage: 'complete', fraction: 1, detail: 'Ready' });
    return { doc_id: F.DOCUMENT.doc_id, version: F.DOCUMENT.version };
  },

  async getParseResult() {
    await wait(220);
    return F.PARSE_RESULT;
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

  async startReview() {
    await wait(150);
    return { job_id: 'job-fixture-1' };
  },

  subscribeReview(_jobId, onEvent): ReviewHandle {
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

  async decideChange() {
    await wait(200);
  },

  async resolveAnchor() {
    await wait(200);
  },

  async getExportManifest() {
    await wait(200);
    return F.EXPORT_MANIFEST;
  },

  exportUrl() {
    return '#fixture-export';
  },
};
