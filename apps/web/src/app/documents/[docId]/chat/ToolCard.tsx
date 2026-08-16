'use client';

import { useState } from 'react';
import { CountStrip } from '@/components/CountStrip';
import { ReferenceCard } from '@/components/ReferenceCard';
import { OrphanMarkerCard } from '@/components/OrphanMarkerCard';
import { FindingCard } from '@/components/FindingCard';
import { ChangeCard } from '@/components/ChangeCard';
import { RejectedOperationCard } from '@/components/RejectedOperationCard';
import { DocumentStructure } from '@/components/DocumentStructure';
import { Plate } from '@/components/Plate';
import { Seal } from '@/components/Seal';
import { RenderedCitation } from '@/components/RenderedCitation';
import type { SourceRecord } from '@/lib/contracts';
import type { ToolPayload } from '@/lib/api/types';

/**
 * A structured tool result, rendered by the component that already renders it.
 *
 * The rule is reuse, not rebuild. A finding in the conversation is the same
 * finding the review feed shows and it gets the same `FindingCard`; a proposed
 * change gets the same `ChangeCard` with the same diff and the same anchor
 * seals. A second `FindingCard` that drifted from the first would be a worse
 * outcome than a slightly awkward prop, which is why `ChangeCard` grew
 * `readOnly` and `DocumentStructure` now takes `sections`.
 *
 * `{card: 'none'}` renders nothing here — the sealed tool line above it already
 * carries the tool's own summary, and a panel saying "no card for this" would be
 * noise where a sentence already did the job.
 *
 * Long results collapse. The agent is asked not to dump the parse result
 * unprompted; the screen must not dump it either, so forty references arrive as
 * three and a disclosure.
 */

const PREVIEW = 3;

export function ToolCard({
  payload,
  sources,
  styleId,
}: {
  payload: ToolPayload;
  sources: Record<string, SourceRecord>;
  styleId: string;
}) {
  switch (payload.card) {
    case 'parse_report':
      return <ParseReport payload={payload.data} styleId={styleId} sources={sources} />;

    case 'outline':
      // Sections that did not carry their blocks cannot drive the outline, and
      // an outline reading "0 paragraphs" for every section would be worse than
      // none. The tool's summary line stands in.
      return payload.data.sections.length === 0 ? null : (
        <Card>
          {payload.data.is_draft && <DraftNote />}
          <DocumentStructure sections={payload.data.sections} />
        </Card>
      );

    case 'findings':
      return (
        <Collapsible
          count={payload.data.findings.length}
          noun="finding"
          render={(shown) => (
            <ul className="space-y-6">
              {payload.data.findings.slice(0, shown).map((f) => (
                <FindingCard
                  key={f.finding_id}
                  finding={f}
                  source={f.source_id ? sources[f.source_id] : undefined}
                  styleId={styleId}
                />
              ))}
            </ul>
          )}
        />
      );

    case 'change_set':
      return (
        <div className="space-y-6">
          <ul className="space-y-6">
            {payload.data.changes.map((ec) => (
              <ChangeCard
                key={ec.change.change_id}
                evaluated={ec}
                sources={sources}
                readOnly
              />
            ))}
          </ul>
          {payload.data.rejected.length > 0 && (
            <div>
              <p className="engraved-label text-muted">Refused by the kernel</p>
              <ul className="mt-4 space-y-6">
                {payload.data.rejected.map((r, i) => (
                  <RejectedOperationCard key={i} rejected={r} />
                ))}
              </ul>
            </div>
          )}
        </div>
      );

    case 'commit':
      return (
        <Plate accent={payload.data.committed ? 'verdigris' : 'madder'} className="px-6 py-6">
          <span
            className={`inline-flex items-center gap-2 font-ui text-xs font-medium ${
              payload.data.committed ? 'text-verdigris' : 'text-madder'
            }`}
          >
            <Seal kind={payload.data.committed ? 'filled' : 'broken'} size={17} />
            {payload.data.committed
              ? `Committed as version ${payload.data.new_version}`
              : 'Nothing was written'}
          </span>
          <p className="measure mt-3 text-xs leading-relaxed text-secondary">
            {payload.data.message}
          </p>
          {/* A change the user approved that then failed to apply is reported,
              never dropped quietly (HR-3). */}
          {Object.entries(payload.data.skipped).length > 0 && (
            <ul className="mt-4 space-y-2">
              {Object.entries(payload.data.skipped).map(([changeId, why]) => (
                <li
                  key={changeId}
                  className="measure border-l-2 border-madder/40 pl-4 text-xs leading-relaxed text-secondary"
                >
                  <span className="font-mono text-2xs text-muted">{changeId}</span> — {why}
                </li>
              ))}
            </ul>
          )}
        </Plate>
      );

    case 'export_manifest':
      return <ManifestCard manifest={payload.data} />;

    case 'exported_file':
      return (
        <Plate accent="verdigris" className="px-6 py-6">
          <span className="inline-flex items-center gap-2 font-ui text-xs font-medium text-verdigris">
            <Seal kind="filled" size={17} />
            Rendered
          </span>
          <p className="mt-3 break-words font-mono text-base text-primary">
            {payload.data.filename}
          </p>
          <p className="mt-2 font-ui text-2xs text-muted">
            {(payload.data.byte_size / 1024).toFixed(0)} KB
            {payload.data.style_id && <> · {payload.data.style_id}</>}
            {payload.data.style_uncertain && (
              <span className="text-sepia">
                {' '}
                · the style was a close call between two candidates
              </span>
            )}
          </p>
          <a
            href={payload.data.download_url}
            download={payload.data.filename}
            className="mt-5 inline-flex items-center gap-3 border border-cobalt/45 bg-leaf px-6 py-3 font-ui text-xs text-cobalt transition-colors duration-ink ease-ink hover:bg-cobalt/[0.06]"
          >
            Download the .tex
          </a>
        </Plate>
      );

    case 'review_progress':
      return <ReviewCounters data={payload.data} />;

    case 'review_plan':
      return <ReviewPlan data={payload.data} />;

    case 'source':
      return (
        <Card>
          <RenderedCitation csl={payload.data.source.csl} styleId={styleId} />
          {/* HR-1: the provenance URL is real and resolvable, because the record
              exists only because a provider adapter saw it in an HTTP response.
              So it is always linked. */}
          {payload.data.source.provenance?.external_url && (
            <p className="mt-3">
              <a
                href={payload.data.source.provenance.external_url}
                target="_blank"
                rel="noreferrer noopener"
                className="font-ui text-2xs text-cobalt underline decoration-cobalt/30 underline-offset-2 hover:decoration-cobalt"
              >
                {payload.data.source.provenance.external_url}
              </a>
            </p>
          )}
        </Card>
      );

    case 'evidence':
      return (
        <Card>
          {/* "The index is still building, these results are partial" is a real
              answer. Silently returning fewer hits is not. */}
          {payload.data.index_status && payload.data.index_status !== 'complete' && (
            <p className="mb-4 inline-flex items-center gap-2 font-ui text-2xs text-sepia">
              <Seal kind="half" size={14} />
              The evidence index is {payload.data.index_status} — these results are partial.
            </p>
          )}
          <Collapsible
            count={payload.data.results.length}
            noun="passage"
            render={(shown) => (
              <ul className="space-y-4">
                {payload.data.results.slice(0, shown).map((hit) => (
                  <li key={`${hit.kind}-${hit.ref_id}`} className="border-l-2 border-cobalt/25 pl-4">
                    <p className="font-ui text-2xs text-muted">
                      {hit.kind} · <span className="font-mono">{hit.ref_id}</span> · similarity{' '}
                      <span className="font-mono text-cobalt">{hit.score.toFixed(2)}</span>
                    </p>
                    <p className="measure mt-1 text-xs leading-relaxed text-secondary">
                      {hit.text}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          />
        </Card>
      );

    case 'section_text':
      return (
        <Card>
          {payload.data.is_draft && <DraftNote />}
          {payload.data.title && (
            <p className="engraved-label mb-3 text-muted">{payload.data.title}</p>
          )}
          <p className="measure whitespace-pre-wrap text-base leading-relaxed text-primary">
            {payload.data.text}
          </p>
        </Card>
      );

    // A progress reading is a number the agent looked up, and the sentence it
    // wrote about it is already on screen above. A card would repeat it.
    case 'parse_progress':
    case 'none':
      return null;
  }
}

function ParseReport({
  payload,
  styleId,
  sources,
}: {
  payload: Extract<ToolPayload, { card: 'parse_report' }>['data'];
  styleId: string;
  sources: Record<string, SourceRecord>;
}) {
  const references = payload.references ?? [];
  const orphans = payload.orphan_markers ?? [];

  return (
    <Card>
      {/*
        The tier counts get Bodoni numerals at headline size in the chat exactly
        as they do on Pl. I. They are the honesty guarantee made visible, and
        demoting them to a sentence in the transcript would be the one place the
        conversational flow quietly promised less than the guided one.

        The strip's filter is inert here: there is no reference list beside it to
        filter, and a control that appears to do something and does not is worse
        than none. `active` is fixed and `onSelect` is a no-op.
      */}
      <CountStrip counts={payload.counts} active="all" onSelect={() => {}} />

      {references.length > 0 && (
        <div className="mt-8">
          <Collapsible
            count={references.length}
            noun="reference"
            render={(shown) => (
              <ul className="space-y-6">
                {references.slice(0, shown).map((ref) => (
                  <ReferenceCard
                    key={ref.ref_id}
                    reference={ref}
                    styleId={styleId}
                    source={ref.source_id ? sources[ref.source_id] : undefined}
                  />
                ))}
              </ul>
            )}
          />
        </div>
      )}

      {orphans.length > 0 && (
        <ul className="mt-6 space-y-6">
          {orphans.map((m, i) => (
            <OrphanMarkerCard key={`${m.marker_text}-${i}`} marker={m} />
          ))}
        </ul>
      )}

      {payload.reconciliation_notes && payload.reconciliation_notes.length > 0 && (
        <ul className="mt-6 space-y-2">
          {payload.reconciliation_notes.map((note, i) => (
            <li key={i} className="measure text-xs leading-relaxed text-secondary">
              {note}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ManifestCard({
  manifest,
}: {
  manifest: Extract<ToolPayload, { card: 'export_manifest' }>['data'];
}) {
  const placeholders = manifest.placeholder_blocks.filter((p) => p.count > 0);

  return (
    <Plate accent={manifest.exportable ? 'cobalt' : 'sepia'} className="px-6 py-6">
      <p className="engraved-label text-muted">Export manifest</p>
      <p className="mt-2 break-words font-mono text-base text-primary">{manifest.filename}</p>

      <dl className="mt-5 grid grid-cols-2 gap-x-8 gap-y-4 font-ui text-2xs">
        <Stat label="Version" value={`v${manifest.version}`} />
        <Stat label="Bibliography" value={`${manifest.bibliography_entries} entries`} />
        <Stat label="Citation style" value={manifest.style_id ?? 'None recorded'} />
        <Stat label="Format" value="LaTeX (.tex)" />
      </dl>

      {/*
        ADR-008, stated before the download rather than discovered inside it.
        This is the placeholder disclosure, and it is the reason `export_latex`
        is a confirm tool rather than a fetch.
      */}
      <div className="mt-6 border border-sepia/40 bg-sepia/[0.05] px-5 py-4">
        <span className="inline-flex items-center gap-2 font-ui text-2xs font-medium text-sepia">
          <Seal kind="half" size={15} />
          Not everything is carried through
        </span>
        <p className="measure mt-2 text-xs leading-relaxed text-secondary">
          {placeholders.length === 0 ? (
            <>
              This paper has no figures, tables or equations, so nothing becomes a placeholder.
            </>
          ) : (
            <>
              {placeholders
                .map((p) => `${p.count} ${p.type}${p.count === 1 ? '' : 's'}`)
                .join(', ')}{' '}
              become placeholders in the .tex — captions kept, content not. Everything else is your
              paper as committed.
            </>
          )}
        </p>
      </div>

      {!manifest.exportable && manifest.blocked_reason && (
        <p className="measure mt-4 text-xs leading-relaxed text-madder">
          {manifest.blocked_reason}
        </p>
      )}
    </Plate>
  );
}

/**
 * The honest secondary counters.
 *
 * `verified / total` in Bodoni at headline size, and beside it the numbers that
 * make a short findings list explicable instead of ambiguous. "4 findings" and
 * "4 findings, 11 candidates killed on the quote check, 6 abstracts
 * unavailable" are different reports about the same run.
 */
function ReviewCounters({
  data,
}: {
  data: Extract<ToolPayload, { card: 'review_progress' }>['data'];
}) {
  const secondary = [
    data.candidates_considered !== null && `${data.candidates_considered} candidates considered`,
    data.quote_check_failures !== null &&
      `${data.quote_check_failures} discarded on the quote check`,
    data.unverifiable_no_abstract !== null &&
      `${data.unverifiable_no_abstract} abstracts unavailable`,
    data.claims_without_candidates !== null &&
      `${data.claims_without_candidates} claims with no candidates at all`,
  ].filter((v): v is string => typeof v === 'string');

  return (
    <Card>
      <p className="flex items-baseline gap-3">
        <span className="font-display text-3xl leading-none tabular-nums text-primary">
          {data.verified}
        </span>
        <span className="font-ui text-xs text-muted">of {data.total} claims verified</span>
      </p>
      {data.findings_emitted !== null && (
        <p className="mt-2 font-ui text-xs text-secondary">
          {data.findings_emitted} finding{data.findings_emitted === 1 ? '' : 's'}
        </p>
      )}
      {secondary.length > 0 && (
        <p className="measure mt-3 font-ui text-2xs leading-relaxed text-muted">
          {secondary.join(' · ')}
        </p>
      )}
    </Card>
  );
}

function ReviewPlan({ data }: { data: Extract<ToolPayload, { card: 'review_plan' }>['data'] }) {
  const missing = (data.all_strategies ?? []).filter((s) => !data.strategies.includes(s));

  return (
    <Card>
      <p className="engraved-label text-muted">What a review will do</p>

      <dl className="mt-4 grid grid-cols-2 gap-x-8 gap-y-4 font-ui text-2xs">
        <Stat
          label="Retrieval strategies"
          value={
            data.all_strategies
              ? `${data.strategies.length} of ${data.all_strategies.length}`
              : `${data.strategies.length}`
          }
        />
        {data.estimated_claims !== null && (
          <Stat label="Claims" value={`about ${data.estimated_claims}`} />
        )}
        {data.rerank_keep !== null && (
          <Stat label="Reranked / verified" value={`${data.rerank_keep} → ${data.verify_keep ?? '—'}`} />
        )}
        {data.estimated_duration_s !== null && (
          <Stat
            label="Expected duration"
            value={`about ${Math.round(data.estimated_duration_s / 60)} minutes`}
          />
        )}
      </dl>

      <ul className="mt-5 flex flex-wrap gap-x-4 gap-y-2">
        {data.strategies.map((s) => (
          <li key={s} className="inline-flex items-center gap-2 font-mono text-2xs text-verdigris">
            <Seal kind="filled" size={13} />
            {s}
          </li>
        ))}
        {/* A strategy that will not run is shown as not running, not omitted.
            An absent key must read as one fewer search, never as thin results. */}
        {missing.map((s) => (
          <li key={s} className="inline-flex items-center gap-2 font-mono text-2xs text-sepia">
            <Seal kind="open" size={13} />
            {s} — not configured
          </li>
        ))}
      </ul>

      {data.notes && data.notes.length > 0 && (
        <ul className="mt-5 space-y-2">
          {data.notes.map((note, i) => (
            <li
              key={i}
              className="measure border-l-2 border-[var(--rule-hair)] pl-4 text-xs leading-relaxed text-secondary"
            >
              {note}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="uppercase tracking-[0.12em] text-muted">{label}</dt>
      <dd className="mt-1 text-xs text-primary">{value}</dd>
    </div>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return <Plate className="px-6 py-6">{children}</Plate>;
}

function DraftNote() {
  return (
    <p className="mb-4 inline-flex items-center gap-2 font-ui text-2xs text-sepia">
      <Seal kind="half" size={14} />
      This is the text as extracted — the bibliography is still being reconciled.
    </p>
  );
}

/** Three, then a disclosure. Forty references are not a transcript entry. */
function Collapsible({
  count,
  noun,
  render,
}: {
  count: number;
  noun: string;
  render: (shown: number) => React.ReactNode;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? count : Math.min(PREVIEW, count);

  return (
    <>
      {render(shown)}
      {count > PREVIEW && (
        <button
          type="button"
          onClick={() => setAll((v) => !v)}
          aria-expanded={all}
          className="mt-4 font-ui text-2xs text-cobalt underline decoration-cobalt/30 underline-offset-2 hover:decoration-cobalt"
        >
          {all
            ? `Show the first ${PREVIEW}`
            : `Show all ${count} ${noun}${count === 1 ? '' : 's'}`}
        </button>
      )}
    </>
  );
}
