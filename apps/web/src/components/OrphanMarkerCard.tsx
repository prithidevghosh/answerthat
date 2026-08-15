import { Plate } from './Plate';
import { StatusBadge } from './StatusBadge';
import { TIER_STATUS } from '@/lib/status';
import type { OrphanMarker } from '@/lib/contracts';

/**
 * An in-text marker whose bibliography entry does not exist.
 *
 * This is the one tier with no reference to show, so the card shows the
 * sentence instead, with the marker picked out inside it — the researcher needs
 * to find this in their manuscript, so the card's job is to locate it.
 */
export function OrphanMarkerCard({ marker }: { marker: OrphanMarker }) {
  const status = TIER_STATUS.orphan_marker;
  const parts = marker.snippet.split(marker.marker_text);

  return (
    <Plate as="li" accent={status.ink} className="px-6 py-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <StatusBadge status={status} />
        <span className="font-mono text-2xs text-muted">{marker.section_title}</span>
      </div>

      <p className="mt-4 text-base leading-relaxed text-primary">
        {parts.length > 1
          ? parts.flatMap((part, i) =>
              i === 0
                ? [part]
                : [
                    <mark
                      key={`m${i}`}
                      className="bg-madder/[0.12] px-1 font-mono text-xs text-madder"
                    >
                      {marker.marker_text}
                    </mark>,
                    part,
                  ],
            )
          : marker.snippet}
      </p>

      <p className="measure mt-3 text-xs leading-relaxed text-secondary">{status.note}</p>

      <p className="mt-4 font-ui text-2xs text-muted">
        Marker <span className="font-mono text-primary">{marker.marker_text}</span> · in{' '}
        {marker.section_title} · span <span className="font-mono">{marker.span_id}</span>
      </p>
    </Plate>
  );
}
