import type {
  ApiStatus,
  ApprovalPayload,
  CommandResult,
  CommitResult,
  ExportManifest,
  ParseResult,
  ParseStatus,
  ReviewEvent,
  ReviewHandle,
  ReviewStarted,
  UploadAccepted,
  UploadProgress,
} from './types';
import type { DocumentIR, SourceRecord } from '../contracts';

/**
 * The one seam between the frontend and the API.
 *
 * Every screen depends on this interface and nothing else, so the live client
 * and the fixture client are interchangeable and neither leaks into components.
 */
export interface ApiClient {
  /** HR-2 probe. Distinguishes "misconfigured" from "not running". */
  getStatus(): Promise<ApiStatus>;

  uploadPdf(
    file: File,
    onProgress: (p: UploadProgress) => void,
    signal?: AbortSignal,
  ): Promise<UploadAccepted>;

  getParseResult(docId: string): Promise<ParseResult>;
  /**
   * The IR itself, at head or at `version`.
   *
   * Distinct from `getParseResult` on purpose: the parse report is a record of one
   * ingest and lives in the API process, so it goes away when the API restarts,
   * while the document is in Postgres and does not. A screen that only needs the
   * title and the version must ask for the document — asking for the parse report
   * makes an editable document unreachable for a reason the user cannot act on.
   */
  getDocument(docId: string, version?: number): Promise<DocumentIR>;
  /** Where an ingest has got to. `null` when the API knows of no such job. */
  getParseStatus(docId: string): Promise<ParseStatus | null>;
  chooseStyle(docId: string, styleId: string): Promise<void>;

  getSource(sourceId: string): Promise<SourceRecord>;

  // NB: .csl files are not fetched through this client. lib/csl/render.ts reads
  // them straight from /csl/, which scripts/sync-csl-styles.mjs copies out of
  // packages/csl-styles at build time. One path to one set of files (HR-4).

  startReview(docId: string): Promise<ReviewStarted>;
  /**
   * Direct EventSource to FastAPI — never proxied through a Next route.
   *
   * Takes the whole `ReviewStarted`, not a job id: the 202 carries the stream
   * URL, and following it is what stops the two halves inventing different ones.
   * The last hand-built URL was `/api/reviews/{job_id}/stream`, which no router
   * has ever served — every review 404'd, and the UI reported it as a review
   * that could not run.
   */
  subscribeReview(started: ReviewStarted, onEvent: (e: ReviewEvent) => void): ReviewHandle;

  sendCommand(docId: string, command: string): Promise<CommandResult>;
  /**
   * The one write in the edit flow. Per-change approve/reject and each orphan's
   * decision travel together in a single request against a single
   * `base_version`, because that is the only thing the API commits (ADR-021) —
   * there is no per-change endpoint, and the two this client used to call
   * (`/documents/{id}/changes/{id}/approve`, `/documents/{id}/anchors/{id}/resolve`)
   * were never served by any router.
   */
  approveChangeSet(changeSetId: string, payload: ApprovalPayload): Promise<CommitResult>;

  getExportManifest(docId: string): Promise<ExportManifest>;
  /** Absolute URL so the browser downloads straight from the API. */
  exportUrl(docId: string): string;
}

const stripTrailingSlash = (u: string) => u.replace(/\/$/, '');

/**
 * Every route the API serves is mounted under /api. Keeping the prefix here,
 * rather than repeating it at ~15 call sites, is what stops the two halves
 * drifting apart again.
 */
const API_PREFIX = '/api';

/**
 * Two origins, because the two runtimes reach the same API by different names.
 *
 * The *browser* must keep talking to the published host port directly —
 * proxying the SSE stream through a Next route handler buffers it and makes
 * findings arrive in a clump (memory.md §3, defeating ADR-014).
 *
 * But Server Components run inside the web container, where that host origin
 * resolves to the container's own loopback and nothing is listening. There the
 * API is the compose service `api`. A single shared constant cannot be correct
 * for both, and using the browser's origin on the server is precisely what made
 * a healthy API report itself unreachable.
 */
const PUBLIC_ORIGIN = stripTrailingSlash(
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000',
);

// Not NEXT_PUBLIC_: this is server-only, and inlining an internal hostname into
// the client bundle would be both useless and misleading. It reads as undefined
// in the browser bundle, which is fine — apiBase() only consults it on the
// server. The fallback keeps `next dev` on the host working with no config.
const INTERNAL_ORIGIN = stripTrailingSlash(process.env.API_INTERNAL_URL ?? PUBLIC_ORIGIN);

/**
 * Browser-facing base. Use this only for URLs the *browser itself* will load:
 * EventSource, XHR uploads, and download links. It is stable across runtimes so
 * that a URL built during SSR still resolves once it reaches the page.
 */
export const API_BASE = `${PUBLIC_ORIGIN}${API_PREFIX}`;

/** Base for a fetch issued by whichever runtime is executing right now. */
export function apiBase(): string {
  return typeof window === 'undefined' ? `${INTERNAL_ORIGIN}${API_PREFIX}` : API_BASE;
}

/**
 * Absolute browser URL for a path **the API itself handed us** — `JobAccepted.poll`,
 * `JobAccepted.stream`, and anything else the server names in a response body.
 *
 * Those paths already carry the `/api` prefix, so they join the *origin*; appending
 * them to `API_BASE` would produce `/api/api/...`. Everything the frontend composes
 * itself still goes through `API_BASE`, which is why both forms are accepted here.
 */
export function browserUrl(pathFromApi: string): string {
  if (/^https?:\/\//i.test(pathFromApi)) return pathFromApi;
  const path = pathFromApi.startsWith('/') ? pathFromApi : `/${pathFromApi}`;
  return path === API_PREFIX || path.startsWith(`${API_PREFIX}/`)
    ? `${PUBLIC_ORIGIN}${path}`
    : `${API_BASE}${path}`;
}

/**
 * Fixtures are opt-in and never the default: if this flag is unset and the API
 * is down, the user sees the configuration screen rather than invented data
 * that reads like a real review. A mocked review presented as a real one is
 * exactly the dishonesty HR-3 exists to prevent.
 */
export const USING_FIXTURES = process.env.NEXT_PUBLIC_USE_FIXTURES === '1';

import { liveClient } from './live-client';
import { fixtureClient } from './fixture-client';

export function getClient(): ApiClient {
  return USING_FIXTURES ? fixtureClient : liveClient;
}
