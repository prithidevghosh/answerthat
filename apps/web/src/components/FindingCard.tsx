'use client';

import { Plate } from './Plate';
import { StatusBadge } from './StatusBadge';
import { Seal } from './Seal';
import { RenderedCitation } from './RenderedCitation';
import { VERIFICATION_STATUS, ABSTRACT_SOURCE_LABEL } from '@/lib/status';
import type { Finding, SourceRecord } from '@/lib/contracts';

const KIND_LABEL: Record<Finding['kind'], string> = {
  missing_work: 'Possibly missing citation',
  claim_citation_mismatch: 'Cited source may not support this claim',
  no_candidates_found: 'No candidates found',
};

const PROVIDER_NAME: Record<SourceRecord['provenance']['provider'], string> = {
  semantic_scholar: 'Semantic Scholar',
  openalex: 'OpenAlex',
  crossref: 'Crossref',
};

/**
 * One finding: the claim, the verification label, the verbatim quote, and a
 * real external link to the source.
 *
 * The quote is the load-bearing element — ADR-006 requires every non-
 * unverifiable verdict to carry a passage that mechanically exists in the
 * fetched abstract, so it is set as a block quotation with a rule, in serif, at
 * reading size. It is evidence the reader is meant to actually read, not a
 * caption.
 */
export function FindingCard({
  finding,
  source,
  styleId,
}: {
  finding: Finding;
  source?: SourceRecord;
  styleId: string;
}) {
  // A search that ran and returned nothing is its own outcome, and must not
  // look like a finding with missing parts (HR-3).
  if (finding.kind === 'no_candidates_found') {
    return <NoCandidatesCard finding={finding} />;
  }

  const status = finding.verification ? VERIFICATION_STATUS[finding.verification.label] : null;

  return (
    <Plate as="li" accent={status?.ink ?? 'sepia'} className="animate-rise-in px-6 py-6 sm:px-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">
          {KIND_LABEL[finding.kind]}
        </span>
        <span className="font-ui text-2xs text-muted">
          citability <span className="font-mono text-primary">{finding.claim.citability.toFixed(2)}</span>
        </span>
      </div>

      {/* The claim, in serif at reading size — this is the researcher's own prose. */}
      <blockquote className="measure mt-4 border-l-2 border-[var(--rule-hair)] pl-5 text-base leading-relaxed text-primary">
        {finding.claim.text}
      </blockquote>

      {status && finding.verification && (
        <>
          <div className="mt-6">
            <StatusBadge status={status} />
            <p className="measure mt-2 text-xs leading-relaxed text-secondary">{status.note}</p>
          </div>

          {finding.verification.quote ? (
            <figure className="mt-5">
              <figcaption className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">
                Quoted from the abstract
              </figcaption>
              {/*
                Verbatim, and checked mechanically against the fetched abstract
                before this finding was allowed to exist (ADR-006). Shown in
                full — never clipped, never summarised.
              */}
              <blockquote className="mt-2 border-l-2 border-indigo/30 bg-paper-deep/60 px-5 py-4 text-base italic leading-relaxed text-primary">
                “{finding.verification.quote}”
              </blockquote>
            </figure>
          ) : (
            <p className="mt-5 font-ui text-2xs text-sepia">
              No quote — {ABSTRACT_SOURCE_LABEL[finding.verification.abstract_source].toLowerCase()}.
            </p>
          )}

          <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 font-ui text-2xs text-muted">
            <div className="flex gap-2">
              <dt>Evidence</dt>
              <dd className="text-primary">
                {ABSTRACT_SOURCE_LABEL[finding.verification.abstract_source]}
              </dd>
            </div>
            {finding.verification.label !== 'unverifiable_no_abstract' && (
              <div className="flex gap-2">
                <dt>Confidence</dt>
                <dd className="font-mono text-primary">
                  {finding.verification.confidence.toFixed(2)}
                </dd>
              </div>
            )}
          </dl>
        </>
      )}

      {source && (
        <div className="mt-6 border-t border-hair pt-5">
          <p className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">Source</p>
          <div className="mt-2">
            <RenderedCitation csl={source.csl} styleId={styleId} />
          </div>
          {/* A real, resolvable URL from the provenance record — HR-1. */}
          <a
            href={source.provenance.external_url}
            target="_blank"
            rel="noreferrer noopener"
            className="mt-3 inline-flex items-center gap-1.5 font-ui text-2xs text-indigo underline decoration-indigo/30 underline-offset-2 hover:decoration-indigo"
          >
            Open on {PROVIDER_NAME[source.provenance.provider]} ↗
          </a>
        </div>
      )}
    </Plate>
  );
}

/**
 * "We searched and found nothing" — deliberately worded and shaped so it can
 * never be confused with "no findings yet" (HR-3).
 */
function NoCandidatesCard({ finding }: { finding: Finding }) {
  return (
    <Plate as="li" accent="sepia" className="animate-rise-in px-6 py-6 sm:px-8">
      <span className="inline-flex items-center gap-2 font-ui text-xs font-medium text-sepia">
        <Seal kind="frame" size={17} />
        No supporting work found for this claim
      </span>

      <blockquote className="measure mt-4 border-l-2 border-[var(--rule-hair)] pl-5 text-base leading-relaxed text-primary">
        {finding.claim.text}
      </blockquote>

      <p className="measure mt-4 text-xs leading-relaxed text-secondary">
        All three retrieval strategies ran against this claim and returned nothing new — no
        passage-level match, no recommendation from your bibliography&apos;s neighbourhood, and no
        result from the citation-graph expansion. That is a genuine result, not a search we skipped:
        it means we could not find work you are missing, not that we did not look.
      </p>
    </Plate>
  );
}
