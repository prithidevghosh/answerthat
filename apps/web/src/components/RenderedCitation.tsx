'use client';

import { useEffect, useState } from 'react';
import { renderBibliographyEntry, CitationRenderError } from '@/lib/csl/render';
import type { CslJson } from '@/lib/contracts';

/**
 * A citation, rendered by citeproc through a real .csl file. HR-4.
 *
 * Prefers `html` pre-rendered by the single-pass bibliography (see
 * useBibliography — one pass is required for correct numbering and name
 * disambiguation). Falls back to rendering this one record alone, which is
 * still citeproc through the same style file, for callers that legitimately
 * have a single record and no bibliography context.
 *
 * There is no fallback that formats the record by hand. If the style cannot
 * render it we say exactly that — a hand-assembled citation would be
 * indistinguishable from a real one, which is the failure HR-4 exists to
 * prevent.
 */
export function RenderedCitation({
  csl,
  styleId,
  html,
  error,
  className = '',
}: {
  csl: CslJson;
  styleId: string;
  html?: string;
  error?: string | null;
  className?: string;
}) {
  const provided = html !== undefined || (error !== undefined && error !== null);
  const [ownHtml, setOwnHtml] = useState<string | null>(null);
  const [ownError, setOwnError] = useState<string | null>(null);

  useEffect(() => {
    if (provided) return;
    let live = true;
    setOwnHtml(null);
    setOwnError(null);
    renderBibliographyEntry(csl, styleId)
      .then((out) => live && setOwnHtml(out))
      .catch(
        (err: unknown) =>
          live &&
          setOwnError(
            err instanceof CitationRenderError ? err.message : `Could not render: ${String(err)}`,
          ),
      );
    return () => {
      live = false;
    };
  }, [csl, styleId, provided]);

  const finalError = error ?? ownError;
  const finalHtml = html ?? ownHtml;

  if (finalError) {
    return (
      <p className={`font-ui text-2xs text-madder ${className}`}>
        This record could not be rendered in {styleId}. {finalError}
      </p>
    );
  }

  if (finalHtml === null || finalHtml === undefined) {
    // Never a formatted-looking placeholder — an unrendered citation must not
    // resemble a rendered one.
    return (
      <p className={`font-ui text-2xs text-muted ${className}`} aria-busy="true">
        Rendering…
      </p>
    );
  }

  return (
    <div
      className={`csl-entry text-base leading-relaxed text-primary ${className}`}
      // citeproc output: its own markup (italics, small-caps), no user input.
      dangerouslySetInnerHTML={{ __html: finalHtml }}
    />
  );
}
