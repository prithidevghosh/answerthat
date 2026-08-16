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
  // The sentence is resolved from the IR and can legitimately be absent. Guarded rather
  // than assumed: this card renders inside a list, and one undefined field throwing here
  // unmounted the entire parse inspector — a page-wide blank for a paper that had parsed
  // perfectly well.
  const parts = marker.snippet ? marker.snippet.split(marker.marker_text) : [];
  const where = marker.section_title ?? marker.section_id;

  return (
    <Plate as="li" accent={status.ink} className="px-6 py-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <StatusBadge status={status} />
        <span className="font-mono text-2xs text-muted">{where}</span>
      </div>

      <p className="mt-4 text-base leading-relaxed text-primary">
        {parts.length > 1 ? (
          parts.flatMap((part, i) =>
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
        ) : marker.snippet ? (
          marker.snippet
        ) : (
          // Not silence, and not an invented sentence: the marker is real and the
          // researcher still has to find it, so we say which span to look in.
          <span className="text-secondary">
            The sentence around this marker could not be located in the parsed document.
            It is in span <span className="font-mono text-xs">{marker.span_id}</span>.
          </span>
        )}
      </p>

      <p className="measure mt-3 text-xs leading-relaxed text-secondary">{status.note}</p>

      <p className="mt-4 font-ui text-2xs text-muted">
        Marker <span className="font-mono text-primary">{marker.marker_text}</span> · in {where} ·
        span <span className="font-mono">{marker.span_id}</span>
      </p>
    </Plate>
  );
}
