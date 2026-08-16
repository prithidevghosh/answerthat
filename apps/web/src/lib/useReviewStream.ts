'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { getClient } from './api/client';
import type { Finding } from './contracts';

export type StreamPhase = 'idle' | 'starting' | 'streaming' | 'interrupted' | 'done' | 'failed';

export interface ReviewState {
  phase: StreamPhase;
  findings: Finding[];
  verified: number;
  total: number | null;
  /** Set while interrupted or failed. Never cleared silently. */
  message: string | null;
}

/**
 * Subscribes to the review SSE stream (ADR-014).
 *
 * Two honesty properties this hook exists to hold:
 *
 *  - **In-progress never reads as complete.** `phase` distinguishes streaming,
 *    interrupted, done and failed, and `verified / total` is always available,
 *    so no caller can render a partial review as a finished one.
 *  - **A dropped stream is surfaced.** EventSource reconnects silently by
 *    design; we move to `interrupted` and say so, because a review that stalled
 *    at 12/47 and looks finished is a false clean bill of health (HR-3).
 *
 * Findings are kept sorted by citability descending — the stream sends them
 * that way, but a reconnect can reorder, and the ordering is the product
 * decision, not an accident of arrival.
 */
export function useReviewStream(docId: string, autoStart: boolean): ReviewState & {
  start: () => void;
} {
  const [state, setState] = useState<ReviewState>({
    phase: 'idle',
    findings: [],
    verified: 0,
    total: null,
    message: null,
  });

  const handleRef = useRef<{ close(): void } | null>(null);
  const startedRef = useRef(false);

  const start = useCallback(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    setState((s) => ({ ...s, phase: 'starting', message: null }));
    const client = getClient();

    client
      .startReview(docId)
      .then((started) => {
        setState((s) => ({ ...s, phase: 'streaming' }));

        // The whole 202, not just its job id: the stream URL is the API's to
        // name, and the client's job is to follow it.
        handleRef.current = client.subscribeReview(started, (event) => {
          setState((prev) => {
            switch (event.type) {
              case 'progress':
                return {
                  ...prev,
                  // A reconnect can replay an earlier progress event; never let
                  // the counter go backwards on screen.
                  verified: Math.max(prev.verified, event.data.verified),
                  total: event.data.total,
                  phase: prev.phase === 'interrupted' ? 'streaming' : prev.phase,
                  message: prev.phase === 'interrupted' ? null : prev.message,
                };

              case 'finding': {
                if (prev.findings.some((f) => f.finding_id === event.data.finding_id)) {
                  return prev; // duplicate after reconnect
                }
                const findings = [...prev.findings, event.data].sort(
                  (a, b) => b.claim.citability - a.claim.citability,
                );
                return {
                  ...prev,
                  findings,
                  phase: prev.phase === 'interrupted' ? 'streaming' : prev.phase,
                  message: prev.phase === 'interrupted' ? null : prev.message,
                };
              }

              case 'done':
                return {
                  ...prev,
                  phase: 'done',
                  verified: event.data.verified,
                  total: event.data.total,
                  message: null,
                };

              case 'error':
                return {
                  ...prev,
                  phase: event.data.recoverable ? 'interrupted' : 'failed',
                  message: event.data.message,
                };
            }
          });
        });
      })
      .catch((err: unknown) => {
        setState((s) => ({
          ...s,
          phase: 'failed',
          message: err instanceof Error ? err.message : String(err),
        }));
      });
  }, [docId]);

  useEffect(() => {
    if (autoStart) start();
  }, [autoStart, start]);

  useEffect(() => () => handleRef.current?.close(), []);

  return { ...state, start };
}
