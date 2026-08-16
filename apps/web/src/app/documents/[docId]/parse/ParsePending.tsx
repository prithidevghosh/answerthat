'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getClient } from '@/lib/api/client';
import type { ParseStatus } from '@/lib/api/types';
import { Plate } from '@/components/Plate';
import { Fleuron } from '@/components/Ornament';

const STAGE_TEXT: Record<string, string> = {
  queued: 'Waiting to start',
  grobid: 'Reading the document structure',
  tei_to_ir: 'Building the document',
  references: 'Segmenting references',
  repair: 'Re-reading references that parsed poorly',
  arbiter: 'Reconciling against Crossref, Semantic Scholar and OpenAlex',
  style: 'Detecting the citation style',
  persist: 'Saving',
  complete: 'Ready',
};

const POLL_INTERVAL_MS = 2000;

/**
 * The paper is still being parsed.
 *
 * Distinct from `LoadFailure`, and that distinction is the point: this screen is
 * reached by opening a document's URL — a reload, a bookmark, a second tab —
 * while its ingest is still running. `/parse` answers 404 until the IR is
 * written, and rendering "Could not load parse results" for that made a healthy
 * parse look like a broken one.
 *
 * It polls the same `parse-status` the upload screen does, and refreshes the
 * route once the parse lands so the inspector appears on its own.
 */
export function ParsePending({ docId, initial }: { docId: string; initial: ParseStatus }) {
  const router = useRouter();
  const [status, setStatus] = useState(initial);

  useEffect(() => {
    if (status.state === 'complete' || status.state === 'failed') return;

    let live = true;
    const timer = setInterval(async () => {
      try {
        const next = await getClient().getParseStatus(docId);
        if (!live || next === null) return;
        setStatus(next);
        // The server component can render the inspector now. Refresh rather than
        // navigate: same URL, and the page re-runs its own fetch.
        if (next.state === 'complete') router.refresh();
      } catch {
        // A single failed poll is not evidence the parse died — the next tick
        // asks again. A parse that genuinely failed comes back as `failed`
        // below, with the reason the API gave.
      }
    }, POLL_INTERVAL_MS);

    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [docId, status.state, router]);

  const pct = status.progress === null ? null : Math.round(status.progress * 100);

  return (
    <main id="main" className="content-column py-24">
      <Plate fleurons className="px-8 py-10">
        <div className="measure">
          <span className="inline-flex items-center gap-3 font-ui text-xs font-medium text-cobalt">
            <Fleuron size={14} className="text-cobalt/60" />
            Still parsing
          </span>

          <p className="mt-5 font-display text-lg text-primary">
            {STAGE_TEXT[status.stage ?? 'queued'] ?? 'Working'}
          </p>

          <div className="mt-5 h-px w-full bg-[var(--rule-hair)]">
            {pct !== null && (
              <div
                className="h-px bg-cobalt transition-[width] duration-ink ease-ink"
                style={{ width: `${pct}%` }}
              />
            )}
          </div>

          <p className="mt-3 font-ui text-2xs text-muted">
            {pct !== null ? `${pct}%` : 'This step does not report progress'} · this page updates
            itself
          </p>

          <p className="mt-6 text-sm leading-relaxed text-secondary">
            Reconciling a bibliography runs at about one request per second against Crossref,
            Semantic Scholar and OpenAlex, so a paper with forty references takes a few minutes.
            Nothing is lost if you close this tab — the parse continues.
          </p>
        </div>
      </Plate>
    </main>
  );
}
