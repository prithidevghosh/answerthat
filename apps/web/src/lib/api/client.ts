import type {
  AnchorResolution,
  ApiStatus,
  CommandResult,
  ExportManifest,
  ParseResult,
  ReviewEvent,
  ReviewHandle,
  UploadAccepted,
  UploadProgress,
} from './types';
import type { SourceRecord } from '../contracts';

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
  chooseStyle(docId: string, styleId: string): Promise<void>;

  getSource(sourceId: string): Promise<SourceRecord>;

  // NB: .csl files are not fetched through this client. lib/csl/render.ts reads
  // them straight from /csl/, which scripts/sync-csl-styles.mjs copies out of
  // packages/csl-styles at build time. One path to one set of files (HR-4).

  startReview(docId: string): Promise<{ job_id: string }>;
  /** Direct EventSource to FastAPI — never proxied through a Next route. */
  subscribeReview(jobId: string, onEvent: (e: ReviewEvent) => void): ReviewHandle;

  sendCommand(docId: string, command: string): Promise<CommandResult>;
  decideChange(docId: string, changeId: string, approve: boolean): Promise<void>;
  resolveAnchor(docId: string, anchorId: string, res: AnchorResolution): Promise<void>;

  getExportManifest(docId: string): Promise<ExportManifest>;
  /** Absolute URL so the browser downloads straight from the API. */
  exportUrl(docId: string): string;
}

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, '') ?? 'http://localhost:8000';

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
