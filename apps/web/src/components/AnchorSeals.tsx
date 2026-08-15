'use client';

import { Seal } from './Seal';
import { shortLabel } from '@/lib/csl/render';
import type { SourceRecord } from '@/lib/contracts';

export type AnchorFate = 'persisted' | 'added' | 'orphaned';

export interface AnchorEntry {
  anchor_id: string;
  source_id: string;
  fate: AnchorFate;
}

const FATE: Record<AnchorFate, { label: string; tone: string; seal: 'filled' | 'open' | 'dangling' }> = {
  persisted: {
    label: 'kept',
    tone: 'text-indigo border-indigo/35 bg-indigo/[0.04]',
    seal: 'filled',
  },
  added: {
    label: 'added',
    tone: 'text-verdigris border-verdigris/40 bg-verdigris/[0.05]',
    seal: 'open',
  },
  orphaned: {
    label: 'needs a decision',
    tone: 'text-madder border-madder/40 bg-madder/[0.05]',
    seal: 'dangling',
  },
};

/**
 * The citation ledger for a proposed change — HR-5 made visible.
 *
 * The guarantee is that no edit shrinks the set of sources reachable from the
 * document without the user approving a removal. That is a claim about a
 * multiset, so the honest way to show it is to show the multiset: every anchor
 * touched by this change, and what happened to it.
 *
 * Seals persist across the diff literally — the same seal, in the same order,
 * is what the user sees before and after, so "the citations survived" is
 * something they can check rather than something we assert.
 */
export function AnchorSeals({
  entries,
  sources,
}: {
  entries: AnchorEntry[];
  sources: Record<string, SourceRecord>;
}) {
  if (entries.length === 0) {
    return (
      <p className="font-ui text-2xs text-muted">
        No citation anchors fall inside this change.
      </p>
    );
  }

  const kept = entries.filter((e) => e.fate === 'persisted').length;
  const added = entries.filter((e) => e.fate === 'added').length;
  const orphaned = entries.filter((e) => e.fate === 'orphaned').length;

  return (
    <div>
      <ul className="flex flex-wrap gap-2">
        {entries.map((entry) => {
          const fate = FATE[entry.fate];
          const source = sources[entry.source_id];
          const label = source ? shortLabel(source.csl) : entry.source_id;
          return (
            <li key={entry.anchor_id}>
              <span
                className={`inline-flex items-center gap-2 rounded border px-2.5 py-1.5 font-ui text-2xs ${fate.tone}`}
                title={`${entry.anchor_id} — ${fate.label}`}
              >
                <Seal kind={fate.seal} size={13} className="shrink-0" />
                <span className="font-medium">{label}</span>
                <span className="opacity-70">{fate.label}</span>
              </span>
            </li>
          );
        })}
      </ul>

      <p className="mt-3 font-ui text-2xs text-muted">
        {kept > 0 && <>{kept} citation{kept === 1 ? '' : 's'} kept</>}
        {kept > 0 && (added > 0 || orphaned > 0) && ' · '}
        {added > 0 && (
          <span className="text-verdigris">
            {added} added
          </span>
        )}
        {added > 0 && orphaned > 0 && ' · '}
        {orphaned > 0 && (
          <span className="text-madder">
            {orphaned} could not be reattached — your decision below
          </span>
        )}
        {orphaned === 0 && <> · none lost</>}
      </p>
    </div>
  );
}
