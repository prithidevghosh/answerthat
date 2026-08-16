import { API_BASE, apiBase, type ApiClient } from './client';
import type {
  AnchorResolution,
  ApiStatus,
  CommandResult,
  ExportManifest,
  ParseResult,
  ParseStatus,
  ReviewEvent,
  ReviewHandle,
  UploadAccepted,
  UploadProgress,
} from './types';
import type { SourceRecord } from '../contracts';

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
 * Upload the PDF, then **wait for the parse it started**.
 *
 * `POST /documents` answers 202 with a job id: the paper is accepted, and GROBID
 * plus per-reference arbitration have not run yet. This used to report
 * `stage: 'complete'` on that 202 and resolve, so the caller navigated to the
 * parse inspector within milliseconds and `GET /documents/{id}/parse` 404'd on a
 * document that did not exist yet — "Could not load parse results", every time,
 * on a paper that was parsing perfectly well and finished a few minutes later.
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
async function uploadPdf(
  file: File,
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<UploadAccepted> {
  const accepted = await postPdf(file, onProgress, signal);

  for (;;) {
    if (signal?.aborted) throw new DOMException('Upload cancelled', 'AbortError');

    const status = await parseStatus(accepted.doc_id);

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
      return { doc_id: accepted.doc_id, version: status.version ?? 1 };
    }

    onProgress({
      stage: STAGE_FROM_BACKEND[status.stage ?? 'queued'] ?? 'extracting',
      // The backend derives this from the stage's position in its own ordered
      // list, so it is a real fraction of the pipeline rather than a timer.
      fraction: status.progress,
      detail: file.name,
    });

    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
}

export const liveClient: ApiClient = {
  getStatus,
  uploadPdf,

  getParseResult: (docId) => json<ParseResult>(`/documents/${docId}/parse`),

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

  startReview: (docId) => json<{ job_id: string }>(`/documents/${docId}/review`, { method: 'POST' }),

  subscribeReview(jobId, onEvent): ReviewHandle {
    // Connected straight to FastAPI. Proxying this through a Next route handler
    // buffers the stream and makes findings arrive in a clump at the end
    // (memory.md §3) — which would defeat the entire point of ADR-014.
    const es = new EventSource(`${API_BASE}/reviews/${jobId}/stream`);

    const forward = (type: ReviewEvent['type']) => (ev: MessageEvent) => {
      try {
        onEvent({ type, data: JSON.parse(ev.data) } as ReviewEvent);
      } catch {
        onEvent({
          type: 'error',
          data: { message: 'The review stream sent a malformed event.', recoverable: false },
        });
      }
    };

    es.addEventListener('progress', forward('progress'));
    es.addEventListener('finding', forward('finding'));
    es.addEventListener('done', (ev) => {
      forward('done')(ev as MessageEvent);
      es.close();
    });
    es.addEventListener('error', () => {
      // EventSource reconnects on its own; we surface the interruption either
      // way, because a stalled review that looks finished is a false clean bill
      // of health (HR-3).
      onEvent({
        type: 'error',
        data: {
          message:
            es.readyState === EventSource.CLOSED
              ? 'The review stream closed before finishing.'
              : 'The review stream was interrupted. Reconnecting…',
          recoverable: es.readyState !== EventSource.CLOSED,
        },
      });
    });

    return { close: () => es.close() };
  },

  sendCommand: (docId, command) =>
    json<CommandResult>(`/documents/${docId}/commands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
    }),

  decideChange: (docId, changeId, approve) =>
    json<void>(`/documents/${docId}/changes/${changeId}/${approve ? 'approve' : 'reject'}`, {
      method: 'POST',
    }),

  resolveAnchor: (docId, anchorId, res) =>
    json<void>(`/documents/${docId}/anchors/${anchorId}/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(res),
    }),

  getExportManifest: (docId) => json<ExportManifest>(`/documents/${docId}/export/manifest`),
  exportUrl: (docId) => `${API_BASE}/documents/${docId}/export.tex`,
};
