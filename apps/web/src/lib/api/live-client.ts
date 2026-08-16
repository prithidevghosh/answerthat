import { API_BASE, apiBase, browserUrl, type ApiClient } from './client';
import { readConfirmation } from './chat-payloads';
import type {
  ApiStatus,
  ChatEvent,
  ChatHandle,
  ChatRole,
  CommandResult,
  CommitResult,
  Conversation,
  ConversationLog,
  ExportManifest,
  JsonObject,
  JsonValue,
  ParseResult,
  ParseStatus,
  ReviewEvent,
  ReviewHandle,
  ReviewStarted,
  UploadAccepted,
  UploadProgress,
} from './types';
import type { DocumentIR, Finding, SourceRecord } from '../contracts';

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  // apiBase(), not API_BASE: these run from Server Components as well as the
  // browser, and the two runtimes reach the API by different hostnames.
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      // A non-JSON error body is itself information; keep the status text.
      detail = res.statusText;
    }
    throw new ApiError(`${init?.method ?? 'GET'} ${path} failed`, res.status, detail);
  }
  return (await res.json()) as T;
}

async function text(path: string): Promise<string> {
  const res = await fetch(`${apiBase()}${path}`);
  if (!res.ok) throw new ApiError(`GET ${path} failed`, res.status);
  return res.text();
}

/**
 * HR-2 has a consequence the frontend has to handle carefully: when the keys
 * are missing the API *refuses to start*, so the honest failure usually looks
 * like a connection refusal, not a 503. We report both, and we never guess that
 * an unreachable API is a configured one.
 */
async function getStatus(): Promise<ApiStatus> {
  try {
    const res = await fetch(`${apiBase()}/health`, { cache: 'no-store' });
    if (res.ok) {
      const body = (await res.json()) as { missing_keys?: string[] };
      const missing = body.missing_keys ?? [];
      return missing.length > 0
        ? { kind: 'config_error', missing_keys: missing, detail: null }
        : { kind: 'ok', missing_keys: [], detail: null };
    }
    const body = (await res.json().catch(() => null)) as {
      missing_keys?: string[];
      detail?: string;
    } | null;
    return {
      kind: 'config_error',
      missing_keys: body?.missing_keys ?? [],
      detail: body?.detail ?? `The API replied ${res.status}.`,
    };
  } catch (err) {
    return {
      kind: 'unreachable',
      missing_keys: [],
      detail: err instanceof Error ? err.message : String(err),
    };
  }
}

/** POST /documents returns 202 with a job id, not a parsed paper. */
interface UploadAcceptedResponse {
  job_id: string;
  doc_id: string;
  poll: string;
}

const parseStatus = (docId: string) =>
  json<ParseStatus>(`/documents/${docId}/parse-status`, { cache: 'no-store' });

/**
 * B1's pipeline stages → the four this UI speaks. The backend's vocabulary is
 * ordered and longer (`queued`, `grobid`, `tei_to_ir`, `references`, `repair`,
 * `arbiter`, `style`, `persist`, `complete`); the mapping is here rather than in
 * the component so the component keeps one small set of names to render.
 */
const STAGE_FROM_BACKEND: Record<string, UploadProgress['stage']> = {
  queued: 'extracting',
  grobid: 'extracting',
  tei_to_ir: 'extracting',
  references: 'parsing',
  repair: 'parsing',
  arbiter: 'resolving',
  style: 'resolving',
  persist: 'resolving',
  complete: 'complete',
};

const POLL_INTERVAL_MS = 1500;

/** XHR, not fetch: upload progress is the one thing fetch still cannot report. */
function postPdf(
  file: File,
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<UploadAcceptedResponse> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/documents`);
    xhr.responseType = 'json';

    xhr.upload.onprogress = (e) => {
      onProgress({
        stage: 'uploading',
        fraction: e.lengthComputable ? e.loaded / e.total : null,
        detail: file.name,
      });
    };
    xhr.upload.onload = () => {
      // Server-side work begins; we genuinely cannot report a fraction for it,
      // so we say so rather than animating a fake bar.
      onProgress({ stage: 'extracting', fraction: null, detail: 'Extracting structure' });
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response as UploadAcceptedResponse);
      } else {
        reject(new ApiError('Upload failed', xhr.status, xhr.response));
      }
    };
    xhr.onerror = () => reject(new ApiError('Upload failed: the API is unreachable.', 0));
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
    signal?.addEventListener('abort', () => xhr.abort(), { once: true });

    xhr.send(form);
  });
}

/**
 * Send the PDF and resolve on the **202**.
 *
 * `POST /documents` answers 202 with a job id: the paper is accepted, and GROBID
 * plus per-reference arbitration have not run yet. **A caller that navigates to
 * the parse inspector on this promise will 404**, every time, on a paper that is
 * parsing perfectly well — which is exactly what used to happen, and why the
 * poll loop below exists. `waitForParse` is not optional garnish on the guided
 * path; it is the thing that makes the guided path work.
 *
 * The two halves are separate because the conversational flow needs the
 * opposite: it navigates on the 202 on purpose, since narrating the parse as it
 * runs is the agent's first job, and its screen is built to show an ingest in
 * progress rather than to read a finished report.
 */
async function uploadPdf(
  file: File,
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<UploadAccepted> {
  const accepted = await postPdf(file, onProgress, signal);
  // No version yet, and none invented: the IR store assigns it at `persist`,
  // several stages away. `waitForParse` reads the real one off `parse-status`.
  return { doc_id: accepted.doc_id, job_id: accepted.job_id, version: null };
}

/**
 * Wait for the parse the upload started.
 *
 * The 202 carries `poll` for exactly this reason, and `parse-status` is the
 * endpoint it names. `resolving` and `parsing` were already in `UploadStage` and
 * nothing had ever emitted them; they light up here.
 *
 * A `failed` parse rejects with the backend's own reason. It never resolves as
 * though a paper were ready (HR-3) — that would send the user to an inspector
 * showing nothing, which reads as a paper with no references rather than as a
 * parse that failed.
 */
async function waitForParse(
  docId: string,
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<UploadAccepted> {
  for (;;) {
    if (signal?.aborted) throw new DOMException('Upload cancelled', 'AbortError');

    const status = await parseStatus(docId);

    if (status.state === 'failed') {
      throw new ApiError(
        status.error ?? 'The parse failed and the API gave no reason.',
        502,
        status,
      );
    }

    if (status.state === 'complete') {
      onProgress({ stage: 'complete', fraction: 1, detail: 'Ready' });
      // The version the store assigned, not one we assumed. `parse-status` only
      // carries it once the IR is written, which is the same moment `/parse`
      // starts answering.
      return { doc_id: docId, job_id: '', version: status.version ?? 1 };
    }

    onProgress({
      stage: STAGE_FROM_BACKEND[status.stage ?? 'queued'] ?? 'extracting',
      // The backend derives this from the stage's position in its own ordered
      // list, so it is a real fraction of the pipeline rather than a timer.
      fraction: status.progress,
      detail: null,
    });

    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
}

/**
 * The `verified / total` counter, off whichever names the payload carries.
 *
 * The endpoint documents `{verified, total}` and the pipeline's own stats are
 * `{claims_verified, claims_total}` — both are read, because a counter that
 * silently resolves to `undefined` renders `NaN of — claims verified`, which is
 * an in-progress review displaying as no review at all (HR-3). A payload with
 * neither is reported as zero rather than as a number we made up.
 */
function counters(data: Record<string, unknown>): { verified: number; total: number } {
  const num = (...keys: string[]): number => {
    for (const key of keys) {
      const value = Number(data[key]);
      if (Number.isFinite(value)) return value;
    }
    return 0;
  };
  return { verified: num('verified', 'claims_verified'), total: num('total', 'claims_total') };
}

/** The server's own words for a failure. Never replaced with a friendlier guess. */
function reason(data: Record<string, unknown>): string {
  const named = [data.message, data.detail, data.error].find(
    (v): v is string => typeof v === 'string' && v.length > 0,
  );
  return named ?? 'The review failed and the API gave no reason.';
}

export const liveClient: ApiClient = {
  getStatus,
  uploadPdf,
  waitForParse,

  getParseResult: (docId) => json<ParseResult>(`/documents/${docId}/parse`),

  getDocument: (docId, version) =>
    json<DocumentIR>(
      `/documents/${docId}${version === undefined ? '' : `?version=${version}`}`,
    ),

  async getParseStatus(docId) {
    try {
      return await parseStatus(docId);
    } catch (err) {
      // 404 means the API knows of no ingest for this document — a real answer,
      // and the caller's cue to stop waiting for one. Any other failure is not
      // ours to interpret as "no job".
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  },

  chooseStyle: (docId, styleId) =>
    json<void>(`/documents/${docId}/style`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ style_id: styleId }),
    }),

  getSource: (sourceId) => json<SourceRecord>(`/sources/${sourceId}`),

  startReview: (docId) =>
    json<ReviewStarted>(`/documents/${docId}/review`, { method: 'POST' }),

  subscribeReview(started, onEvent): ReviewHandle {
    // Connected straight to FastAPI. Proxying this through a Next route handler
    // buffers the stream and makes findings arrive in a clump at the end
    // (memory.md §3) — which would defeat the entire point of ADR-014.
    //
    // The URL is the one the 202 named, not one composed here. Composing it is
    // how this broke: the client asked for `/api/reviews/{job_id}/stream` while
    // the router served `/api/documents/{doc_id}/review/stream`, so every review
    // 404'd. Note the identifier as well as the shape — the runner keys jobs by
    // *document*, and a job id would not have resolved even on the right path.
    const url = browserUrl(started.stream ?? `/documents/${started.doc_id}/review/stream`);
    const es = new EventSource(url);

    let closed = false;
    const close = () => {
      closed = true;
      es.close();
    };

    const emit = (event: ReviewEvent) => {
      if (!closed) onEvent(event);
    };

    const parse = (ev: MessageEvent): Record<string, unknown> | null => {
      try {
        return JSON.parse(ev.data) as Record<string, unknown>;
      } catch {
        return null;
      }
    };

    const malformed = () =>
      emit({
        type: 'error',
        data: { message: 'The review stream sent a malformed event.', recoverable: false },
      });

    es.addEventListener('progress', (ev) => {
      const data = parse(ev as MessageEvent);
      if (!data) return malformed();
      emit({ type: 'progress', data: counters(data) });
    });

    es.addEventListener('finding', (ev) => {
      const data = parse(ev as MessageEvent);
      if (!data) return malformed();
      emit({ type: 'finding', data: data as unknown as Finding });
    });

    // The wire says `complete`; this client's vocabulary says `done`. The two
    // disagreed, so the terminal event was never recognised: the stream reached
    // its end, the server closed the connection, and the UI reported "closed
    // before finishing" on a review that had in fact finished.
    const finish = (ev: MessageEvent) => {
      const data = parse(ev);
      if (!data) return malformed();
      emit({ type: 'done', data: counters(data) });
      close();
    };
    es.addEventListener('complete', (ev) => finish(ev as MessageEvent));
    es.addEventListener('done', (ev) => finish(ev as MessageEvent));

    es.addEventListener('error', (ev) => {
      // Two different events arrive here. A *named* `error` event from the server
      // is a MessageEvent carrying the reason, and it is terminal — the endpoint
      // closes the stream after it. A transport error is a bare Event, and
      // EventSource may still reconnect. Treating the first as the second is what
      // turned a named backend failure into "reconnecting…" forever (HR-3).
      const data = 'data' in ev ? parse(ev as MessageEvent) : null;
      if (data) {
        emit({
          type: 'error',
          data: { message: reason(data), recoverable: false },
        });
        close();
        return;
      }

      emit({
        type: 'error',
        data: {
          message:
            es.readyState === EventSource.CLOSED
              ? 'The review stream closed before finishing.'
              : 'The review stream was interrupted. Reconnecting…',
          recoverable: es.readyState !== EventSource.CLOSED,
        },
      });
      // A closed EventSource never reopens; stop pretending it might.
      if (es.readyState === EventSource.CLOSED) close();
    });

    return { close };
  },

  sendCommand: (docId, command) =>
    json<CommandResult>(`/documents/${docId}/commands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
    }),

  // The change set is addressed by its own id, not the document's: the id is what
  // the API handed back, and it is what pins the approval to the proposal the user
  // actually read.
  approveChangeSet: (changeSetId, payload) =>
    json<CommitResult>(`/change-sets/${changeSetId}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  getExportManifest: (docId) => json<ExportManifest>(`/documents/${docId}/export/manifest`),
  exportUrl: (docId) => `${API_BASE}/documents/${docId}/export.tex`,

  // ---------- the conversational flow ----------

  startConversation: (docId) =>
    json<Conversation>(`/documents/${docId}/chat`, { method: 'POST' }),

  getConversation: (conversationId) => json<ConversationLog>(`/chat/${conversationId}`),

  sendMessage: (conversationId, text) =>
    json<{ accepted: true }>(`/chat/${conversationId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }),

  async stopTurn(conversationId) {
    await json<unknown>(`/chat/${conversationId}/stop`, { method: 'POST' });
  },

  subscribeChat(conv, onEvent): ChatHandle {
    // Straight to FastAPI, and following the URL the server named. Both halves
    // of that sentence are load-bearing, and both were learned from the review
    // stream: a Next route handler buffers SSE and delivers the agent's whole
    // turn in one clump at the end, and a hand-composed path 404s silently.
    const url = browserUrl(conv.stream);
    const es = new EventSource(url);

    let closed = false;
    const close = () => {
      closed = true;
      es.close();
    };

    const emit = (event: ChatEvent) => {
      if (!closed) onEvent(event);
    };

    const parse = (ev: MessageEvent): JsonObject | null => {
      try {
        const parsed: unknown = JSON.parse(ev.data as string);
        return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
          ? (parsed as JsonObject)
          : null;
      } catch {
        return null;
      }
    };

    const malformed = (event: string) =>
      emit({
        type: 'error',
        data: {
          message: `The conversation stream sent a malformed ${event} event.`,
          detail: null,
          recoverable: false,
        },
      });

    const str = (v: JsonValue | undefined, fallback = ''): string =>
      typeof v === 'string' ? v : fallback;
    const numOrNull = (v: JsonValue | undefined): number | null =>
      typeof v === 'number' && Number.isFinite(v) ? v : null;
    const obj = (v: JsonValue | undefined): JsonObject | null =>
      typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as JsonObject) : null;

    const role = (v: JsonValue | undefined): ChatRole =>
      v === 'user' || v === 'assistant' || v === 'tool' || v === 'system_notice' ? v : 'assistant';

    const on = (name: string, handle: (data: JsonObject) => void) => {
      es.addEventListener(name, (ev) => {
        const data = parse(ev as MessageEvent);
        if (!data) return malformed(name);
        handle(data);
      });
    };

    on('message_start', (d) =>
      emit({
        type: 'message_start',
        data: { message_id: str(d.message_id), role: role(d.role) },
      }),
    );

    on('message_delta', (d) =>
      emit({
        type: 'message_delta',
        data: { message_id: str(d.message_id), text: str(d.text) },
      }),
    );

    on('message', (d) =>
      emit({
        type: 'message',
        data: {
          message_id: str(d.message_id),
          role: role(d.role),
          content: str(d.content),
        },
      }),
    );

    on('tool_call', (d) =>
      emit({
        type: 'tool_call',
        data: {
          call_id: str(d.call_id),
          name: str(d.name),
          arguments: obj(d.arguments) ?? {},
          // A tool the registry gave no label for still gets a readable line —
          // its own name beats an empty one, and both beat hiding the call.
          label: str(d.label) || str(d.name),
        },
      }),
    );

    on('tool_result', (d) =>
      emit({
        type: 'tool_result',
        data: {
          call_id: str(d.call_id),
          name: str(d.name),
          ok: d.ok === true,
          summary: str(d.summary),
          data: obj(d.data),
          error: typeof d.error === 'string' ? d.error : null,
        },
      }),
    );

    on('progress', (d) => {
      if (d.kind === 'review') {
        // The watcher forwards the review stream's `finding` events under
        // `progress` too, tagged `event: "finding"`. Those payloads are a
        // Finding — they carry no counters at all — so reading `verified` and
        // `total` off one yields zeroes, and the live counter drops to
        // "0 of 0 claims verified" every time a finding lands. That reads as a
        // review that verified nothing, which is the precise dishonesty the
        // counter exists to prevent. A payload with no counters is not a
        // progress reading and is dropped; findings reach the screen as
        // `FindingCard`s off the agent's `list_findings` result.
        //
        // Both vocabularies are accepted for the same reason `counters()`
        // above accepts both: the endpoint documents `{verified, total}` and
        // the pipeline's own stats are `{claims_verified, claims_total}`.
        const verified = numOrNull(d.verified) ?? numOrNull(d.claims_verified);
        const total = numOrNull(d.total) ?? numOrNull(d.claims_total);
        if (verified === null || total === null) return;

        emit({
          type: 'progress',
          data: {
            kind: 'review',
            verified,
            total,
            findings_emitted: numOrNull(d.findings_emitted),
            candidates_considered: numOrNull(d.candidates_considered),
            quote_check_failures: numOrNull(d.quote_check_failures),
            unverifiable_no_abstract: numOrNull(d.unverifiable_no_abstract),
            claims_without_candidates: numOrNull(d.claims_without_candidates),
            error: typeof d.error === 'string' ? d.error : null,
          },
        });
        return;
      }
      // Anything not named `review` is the ingest. The watcher only emits two
      // kinds, and defaulting to the one the screen shows first is safer than
      // dropping a tick because a kind arrived misspelt.
      const state = d.state;
      emit({
        type: 'progress',
        data: {
          kind: 'parse',
          state:
            state === 'queued' || state === 'running' || state === 'complete' || state === 'failed'
              ? state
              : null,
          stage: typeof d.stage === 'string' ? d.stage : null,
          fraction: numOrNull(d.fraction),
          filename: typeof d.filename === 'string' ? d.filename : null,
          error: typeof d.error === 'string' ? d.error : null,
        },
      });
    });

    on('awaiting_confirmation', (d) =>
      emit({ type: 'awaiting_confirmation', data: readConfirmation(d) }),
    );

    on('heartbeat', () => emit({ type: 'heartbeat' }));

    // The wire says `done`. `complete` is listened for as well because the
    // review stream's terminal event was missed for exactly this reason — two
    // vocabularies, no overlap, and a finished run reported as "closed before
    // finishing". Registering both costs nothing and cannot be wrong.
    const finish = (d: JsonObject) => {
      emit({
        type: 'done',
        data: {
          message_id: typeof d.message_id === 'string' ? d.message_id : null,
          tokens_used: numOrNull(d.tokens_used),
          budget_remaining: numOrNull(d.budget_remaining),
        },
      });
    };
    on('done', finish);
    on('complete', finish);

    es.addEventListener('error', (ev) => {
      // Two different events arrive here, and the distinction is the whole
      // point. A *named* `error` from the server is a MessageEvent carrying a
      // reason, and it is terminal for the turn — but not for the conversation,
      // so the transcript stays and the composer re-enables. A transport error
      // is a bare Event, and EventSource may still reconnect on its own.
      const data = 'data' in ev ? parse(ev as MessageEvent) : null;
      if (data) {
        emit({
          type: 'error',
          data: {
            message: str(data.error) || str(data.message) || 'The turn failed and the API gave no reason.',
            detail: typeof data.detail === 'string' ? data.detail : null,
            recoverable: false,
          },
        });
        return;
      }

      emit({
        type: 'error',
        data: {
          message:
            es.readyState === EventSource.CLOSED
              ? 'The conversation stream closed.'
              : 'The conversation stream was interrupted. Reconnecting…',
          detail: null,
          recoverable: es.readyState !== EventSource.CLOSED,
        },
      });
      // A closed EventSource never reopens; stop pretending it might.
      if (es.readyState === EventSource.CLOSED) close();
    });

    return { close };
  },
};
