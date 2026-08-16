'use client';

import { useState } from 'react';
import { Plate } from './Plate';
import { Seal } from './Seal';
import { getClient } from '@/lib/api/client';
import type { StyleDetection } from '@/lib/api/types';

const STYLE_NAME: Record<string, string> = {
  ieee: 'IEEE',
  apa: 'APA 7th edition',
  'acm-sig-proceedings': 'ACM (SIG Proceedings)',
  nature: 'Nature',
  'chicago-author-date': 'Chicago (author–date)',
  vancouver: 'Vancouver',
};

export const styleName = (id: string) => STYLE_NAME[id] ?? id;

/**
 * Detected citation style, with its score (ADR-011: show the score).
 *
 * When the top two round-trip scores fall within 0.05 the detector declares
 * `ambiguous` rather than guessing, and the user picks. That branch is a
 * genuine decision point, so it is a card with buttons — not a warning to
 * dismiss.
 */
export function StyleBanner({
  docId,
  style,
  inUse,
  onChosen,
}: {
  docId: string;
  style: StyleDetection;
  /** The style actually applied to the document (ADR-030), which on a tie is the
   *  detector's closest candidate rather than its null verdict. */
  inUse?: string | null;
  onChosen: (styleId: string) => void;
}) {
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function choose(styleId: string) {
    setSaving(styleId);
    setError(null);
    try {
      await getClient().chooseStyle(docId, styleId);
      onChosen(styleId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  }

  if (style.ambiguous) {
    const top = style.candidates.slice(0, 2);
    return (
      <Plate accent="sepia" className="px-6 py-6">
        <span className="inline-flex items-center gap-2 font-ui text-xs font-medium text-sepia">
          <Seal kind="half" size={17} />
          {inUse
            ? `Rendering in ${styleName(inUse)} — this was a close call`
            : 'Citation style is ambiguous — please choose'}
        </span>

        {/*
          ADR-030: a tie no longer blocks. The closest candidate is applied and said
          out loud here, so this screen and the export screen tell the same story —
          they used to disagree, one asking the user to choose while the other
          reported a style already in use.
        */}
        <p className="measure mt-3 text-xs leading-relaxed text-secondary">
          We render your references through each candidate style and compare the result to the raw
          strings in your document. Two styles scored within 0.05 of each other, which is too close
          to call{inUse ? ', so we used the closer one' : ''}. Picking the wrong one would
          reformat your bibliography, so we would rather say so than let you find out in the
          exported file.
        </p>

        <div className="mt-5 flex flex-wrap gap-3">
          {top.map((c) => (
            <button
              key={c.style_id}
              type="button"
              disabled={saving !== null}
              onClick={() => choose(c.style_id)}
              className="rounded border border-sepia/40 px-4 py-2 font-ui text-xs text-primary transition-colors duration-ink ease-ink hover:bg-sepia/[0.07] disabled:opacity-50"
            >
              {saving === c.style_id ? 'Saving…' : styleName(c.style_id)}
              <span className="ml-2 font-mono text-2xs text-muted">{c.score.toFixed(2)}</span>
              {c.style_id === inUse && (
                <span className="ml-2 font-ui text-2xs text-cobalt">in use</span>
              )}
            </button>
          ))}
        </div>

        {error && (
          <p role="alert" className="mt-3 font-ui text-2xs text-madder">
            Could not save that choice: {error}
          </p>
        )}
      </Plate>
    );
  }

  if (!style.style_id) {
    return (
      <Plate accent="madder" className="px-6 py-6">
        <span className="inline-flex items-center gap-2 font-ui text-xs font-medium text-madder">
          <Seal kind="broken" size={17} />
          No citation style could be detected
        </span>
        <p className="measure mt-3 text-xs leading-relaxed text-secondary">
          None of the six candidate styles produced a close enough match to your reference strings.
          Citations below are shown in their raw form. Choosing a style manually will let us render
          them.
        </p>
      </Plate>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-ui text-2xs text-muted">
      <span className="inline-flex items-center gap-2 text-cobalt">
        <Seal kind="filled" size={14} />
        Rendered in {styleName(style.style_id)}
      </span>
      {style.score !== null && (
        <span>
          round-trip similarity <span className="font-mono text-primary">{style.score.toFixed(2)}</span>
        </span>
      )}
    </div>
  );
}
