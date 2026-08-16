'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { CountStrip } from '@/components/CountStrip';
import { ReferenceCard } from '@/components/ReferenceCard';
import { OrphanMarkerCard } from '@/components/OrphanMarkerCard';
import { DocumentStructure } from '@/components/DocumentStructure';
import { StyleBanner } from '@/components/StyleBanner';
import { RuleWithFleuron } from '@/components/Ornament';
import { useBibliography } from '@/lib/csl/useBibliography';
import { TIER_STATUS, TIER_ORDER, TIER_COUNT_LABEL } from '@/lib/status';
import type { ConfidenceTier, SourceRecord } from '@/lib/contracts';
import type { ParseResult } from '@/lib/api/types';

export function ParseInspector({
  docId,
  result,
  sources,
}: {
  docId: string;
  result: ParseResult;
  sources: Record<string, SourceRecord>;
}) {
  const [filter, setFilter] = useState<ConfidenceTier | 'all'>('all');
  // The document's resolved style first. `result.style.style_id` is the detector's raw
  // verdict and is null on a tie, which sent this to 'ieee' — numbering the bibliography
  // of an author-date paper — for exactly the documents least able to survive the guess.
  const [styleId, setStyleId] = useState(
    result.document.metadata.style_id ?? result.style?.style_id ?? 'chicago-author-date',
  );

  // One citeproc pass over the whole bibliography, always in document order, so
  // numeric styles number correctly and author-date styles disambiguate. Cards
  // then look up their own entry regardless of the order they are displayed in.
  const bibInput = useMemo(
    () => result.references.map((r) => ({ key: r.ref_id, csl: r.csl })),
    [result.references],
  );
  const bibliography = useBibliography(bibInput, styleId);

  const visible = useMemo(
    () => (filter === 'all' ? result.references : result.references.filter((r) => r.tier === filter)),
    [filter, result.references],
  );

  const showOrphans = filter === 'all' || filter === 'orphan_marker';

  // Failures first. A researcher opening this screen needs the things that went
  // wrong, not forty resolved references they must scroll past to find them.
  const ordered = useMemo(() => {
    const rank: Record<ConfidenceTier, number> = {
      quarantined: 0,
      low_confidence: 1,
      parsed_unresolved: 2,
      orphan_marker: 3,
      resolved: 4,
    };
    return [...visible].sort((a, b) => rank[a.tier] - rank[b.tier]);
  }, [visible]);

  return (
    <main id="main" className="relative z-10 content-column py-16">
      <div className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <p className="engraved-label text-muted">Parse inspector</p>
          <h1 className="mt-2 font-display text-3xl text-primary">
            {result.document.metadata.title ?? 'Untitled document'}
          </h1>
        </div>
        <Link
          href={`/documents/${docId}/review`}
          className="rounded border border-cobalt/40 px-5 py-2.5 font-ui text-xs text-cobalt transition-colors duration-ink ease-ink hover:bg-cobalt/[0.06]"
        >
          Start review →
        </Link>
      </div>

      <div className="mt-12">
        <CountStrip counts={result.counts} active={filter} onSelect={setFilter} />
      </div>

      {/* No banner when the detector is unbound. The references still render in the
          fallback style — silently, because claiming a detected style we never
          detected would be worse than showing none. */}
      {result.style && (
        <div className="mt-8">
          <StyleBanner
            docId={docId}
            style={result.style}
            inUse={result.document.metadata.style_id}
            onChosen={setStyleId}
          />
        </div>
      )}

      <RuleWithFleuron className="my-16" />

      {/* Two columns inside the content column: structure left, references
          right. The margin ornament stays outside, at reduced strength. */}
      <div className="grid gap-16 lg:grid-cols-[minmax(0,260px)_minmax(0,1fr)]">
        <aside>
          <h2 className="engraved-label text-muted">
            Document structure
          </h2>
          <div className="mt-6">
            <DocumentStructure document={result.document} />
          </div>
        </aside>

        <section aria-labelledby="refs-heading">
          <div className="flex flex-wrap items-baseline justify-between gap-4">
            <h2 id="refs-heading" className="engraved-label text-muted">
              References
            </h2>
            {filter !== 'all' && (
              <button
                type="button"
                onClick={() => setFilter('all')}
                className="font-ui text-2xs text-cobalt underline decoration-cobalt/30 underline-offset-2 hover:decoration-cobalt"
              >
                Showing {TIER_COUNT_LABEL[filter]} only — show all
              </button>
            )}
          </div>

          <FilterChips counts={result.counts} active={filter} onSelect={setFilter} />

          <ul className="mt-8 space-y-6">
            {showOrphans &&
              result.orphan_markers.map((m) => (
                <OrphanMarkerCard key={m.anchor_id} marker={m} />
              ))}

            {ordered.map((ref) => (
              <ReferenceCard
                key={ref.ref_id}
                reference={ref}
                styleId={styleId}
                source={ref.source_id ? sources[ref.source_id] : undefined}
                renderedHtml={bibliography.entries[ref.ref_id]}
                renderError={bibliography.error}
              />
            ))}
          </ul>

          {ordered.length === 0 && (!showOrphans || result.orphan_markers.length === 0) && (
            <p className="mt-8 text-xs text-secondary">
              No references in this tier. That is a real result, not an empty screen — use “show
              all” above to see the other tiers.
            </p>
          )}
        </section>
      </div>
    </main>
  );
}

function FilterChips({
  counts,
  active,
  onSelect,
}: {
  counts: import('@/lib/api/types').TierCounts;
  active: ConfidenceTier | 'all';
  onSelect: (t: ConfidenceTier | 'all') => void;
}) {
  const available = TIER_ORDER.filter((t) => counts[t] > 0);
  return (
    <div className="mt-4 flex flex-wrap gap-2">
      <Chip label={`All ${counts.total_detected + counts.orphan_marker}`} active={active === 'all'} onClick={() => onSelect('all')} />
      {available.map((tier) => (
        <Chip
          key={tier}
          label={`${counts[tier]} ${TIER_COUNT_LABEL[tier]}`}
          active={active === tier}
          ink={TIER_STATUS[tier].ink}
          onClick={() => onSelect(tier)}
        />
      ))}
    </div>
  );
}

function Chip({
  label,
  active,
  ink,
  onClick,
}: {
  label: string;
  active: boolean;
  ink?: 'cobalt' | 'sepia' | 'madder' | 'verdigris';
  onClick: () => void;
}) {
  // Full class strings per ink: Tailwind scans literals, and `currentColor`
  // takes no alpha modifier, so both halves must be spelled out.
  const tone =
    ink === 'madder'
      ? {
          base: 'text-madder border-madder/35',
          on: 'bg-madder/[0.09]',
          off: 'hover:bg-madder/[0.05]',
        }
      : ink === 'sepia'
        ? {
            base: 'text-sepia border-sepia/35',
            on: 'bg-sepia/[0.09]',
            off: 'hover:bg-sepia/[0.05]',
          }
        : {
            base: 'text-cobalt border-cobalt/35',
            on: 'bg-cobalt/[0.09]',
            off: 'hover:bg-cobalt/[0.05]',
          };
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded border px-3 py-1.5 font-ui text-2xs transition-colors duration-ink ease-ink ${
        tone.base
      } ${active ? tone.on : tone.off}`}
    >
      <span className={active ? 'font-semibold' : ''}>{label}</span>
    </button>
  );
}
