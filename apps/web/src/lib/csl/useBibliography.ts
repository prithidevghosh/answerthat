'use client';

import { useEffect, useState } from 'react';
import { Cite, plugins } from '@citation-js/core';
import '@citation-js/plugin-csl';
import { ensureStyle, CitationRenderError } from './render';
import type { CslJson } from '../contracts';

/**
 * Renders the whole bibliography in ONE citeproc pass and returns a map from
 * entry id to rendered HTML.
 *
 * This is not an optimisation — it is a correctness requirement. citeproc
 * numbers entries and disambiguates names *across the bibliography*, so
 * rendering each reference in isolation produces "[1]" on every numeric entry
 * and drops the a/b suffixes that author-date styles need to tell "Smith 2020"
 * from "Smith 2020". Per-entry rendering looks right in a single card and is
 * wrong on the page.
 *
 * Entries are always rendered in document order, whatever order the UI shows
 * them in, so a card's number matches the user's actual bibliography.
 */
export interface BibliographyResult {
  entries: Record<string, string>;
  error: string | null;
  ready: boolean;
}

export function useBibliography(
  records: { key: string; csl: CslJson | null }[],
  styleId: string,
): BibliographyResult {
  const [result, setResult] = useState<BibliographyResult>({
    entries: {},
    error: null,
    ready: false,
  });

  // Re-render only when the actual inputs change, not on every parent render.
  const signature = records.map((r) => r.key).join('|') + '::' + styleId;

  useEffect(() => {
    let live = true;
    setResult({ entries: {}, error: null, ready: false });

    (async () => {
      await ensureStyle(styleId);

      const renderable = records.filter(
        (r): r is { key: string; csl: CslJson } => r.csl !== null,
      );
      if (renderable.length === 0) {
        if (live) setResult({ entries: {}, error: null, ready: true });
        return;
      }

      // citeproc keys output by the entry id, so each record is given one we
      // control and can map back.
      const cite = new Cite(renderable.map((r) => ({ ...r.csl, id: r.key })));
      const html = cite.format('bibliography', {
        format: 'html',
        template: styleId,
        lang: 'en-US',
      });

      // Split the single blob back into per-entry HTML using the
      // data-csl-entry-id attribute citeproc emits on each .csl-entry.
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const entries: Record<string, string> = {};
      doc.querySelectorAll('.csl-entry').forEach((el) => {
        const id = el.getAttribute('data-csl-entry-id');
        if (id) entries[id] = el.innerHTML;
      });

      if (live) setResult({ entries, error: null, ready: true });
    })().catch((err: unknown) => {
      if (!live) return;
      setResult({
        entries: {},
        error:
          err instanceof CitationRenderError
            ? err.message
            : `Could not render the bibliography: ${String(err)}`,
        ready: true,
      });
    });

    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  return result;
}

// Re-exported so callers that already depend on this module do not also need
// to reach into ./render for the plugin registration side effects.
export { plugins };
