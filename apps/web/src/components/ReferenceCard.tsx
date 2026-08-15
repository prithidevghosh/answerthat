'use client';

import { useState } from 'react';
import { Plate } from './Plate';
import { StatusBadge } from './StatusBadge';
import { RenderedCitation } from './RenderedCitation';
import { TIER_STATUS } from '@/lib/status';
import type { ParsedReference, SourceRecord } from '@/lib/contracts';

/**
 * One reference, at whatever tier it reached.
 *
 * The five tiers share one card: same plate, same fleurons, same spacing, same
 * type. What differs is the ink of the hairline, the seal, the label, and what
 * evidence the card is obliged to show. A quarantined reference is not a
 * degraded resolved reference — it is a first-class outcome with its own
 * evidence (the raw string, verbatim), and it is laid out with the same care.
 */
export function ReferenceCard({
  reference,
  styleId,
  source,
  renderedHtml,
  renderError,
}: {
  reference: ParsedReference;
  styleId: string;
  source?: SourceRecord;
  /** Pre-rendered by the single-pass bibliography — see useBibliography. */
  renderedHtml?: string;
  renderError?: string | null;
}) {
  const status = TIER_STATUS[reference.tier];
  const [showRaw, setShowRaw] = useState(false);

  // Quarantined entries have no parse to show, so the raw string is not
  // supplementary detail — it is the only evidence, and it is always open.
  const rawAlwaysVisible = reference.tier === 'quarantined';

  return (
    <Plate as="li" accent={status.ink} className="px-6 py-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <StatusBadge status={status} />
        <span className="font-mono text-2xs text-muted">{reference.ref_id}</span>
      </div>

      <div className="mt-4">
        {reference.csl ? (
          <RenderedCitation
            csl={reference.csl}
            styleId={styleId}
            html={renderedHtml}
            error={renderError}
          />
        ) : (
          <p className="text-base italic text-secondary">
            No structured record — we could not read fields from this entry.
          </p>
        )}
      </div>

      <p className="measure mt-3 text-xs leading-relaxed text-secondary">{status.note}</p>

      {/* Scores are shown as numbers, not as a vague word. ADR-011's principle:
          show the score, let the researcher judge it. */}
      <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 font-ui text-2xs text-muted">
        <div className="flex gap-2">
          <dt>Parse confidence</dt>
          <dd className="font-mono text-primary">{reference.parse_confidence.toFixed(2)}</dd>
        </div>
        {reference.agreement_score !== null && reference.agreement_score !== undefined && (
          <div className="flex gap-2">
            <dt>Agreement</dt>
            <dd className="font-mono text-primary">{reference.agreement_score.toFixed(2)}</dd>
          </div>
        )}
      </dl>

      {source && (
        <p className="mt-4">
          <a
            href={source.provenance.external_url}
            target="_blank"
            rel="noreferrer noopener"
            className="font-ui text-2xs text-cobalt underline decoration-cobalt/30 underline-offset-2 hover:decoration-cobalt"
          >
            View on {PROVIDER_NAME[source.provenance.provider]} ↗
          </a>
        </p>
      )}

      {(rawAlwaysVisible || showRaw) && (
        <div className="mt-6">
          <p className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">
            Raw string, exactly as it appeared in your document
          </p>
          {/*
            Verbatim and never truncated: no ellipsis, no line clamp, no
            max-height. It wraps and the card grows. This string is the evidence
            that we did not invent anything, and a reader must be able to
            compare it against their PDF character by character.
          */}
          <pre className="mt-2 whitespace-pre-wrap break-words rounded border border-hair bg-paper-deep px-4 py-3 font-mono text-xs leading-relaxed text-primary">
            {reference.raw_string}
          </pre>
        </div>
      )}

      {!rawAlwaysVisible && (
        <button
          type="button"
          onClick={() => setShowRaw((v) => !v)}
          aria-expanded={showRaw}
          className="mt-4 font-ui text-2xs text-cobalt underline decoration-cobalt/30 underline-offset-2 hover:decoration-cobalt"
        >
          {showRaw ? 'Hide raw string' : 'Show raw string'}
        </button>
      )}
    </Plate>
  );
}

const PROVIDER_NAME: Record<SourceRecord['provenance']['provider'], string> = {
  semantic_scholar: 'Semantic Scholar',
  openalex: 'OpenAlex',
  crossref: 'Crossref',
};
