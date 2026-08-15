import type { SealKind } from '@/lib/status';

/**
 * Engraved wax-seal marks, one per status.
 *
 * These carry real information, so they are drawn to be told apart by *shape*
 * at a glance — filled, open, half, broken, dangling — not by their ink. A
 * reader with no colour vision, or a printed page, loses nothing. They are
 * always rendered beside a text label, never alone (design-system.md §7).
 *
 * Decorative-only ornament lives in Ornament.tsx and is aria-hidden; these are
 * not that, so they take a title when used without adjacent text.
 */
export function Seal({
  kind,
  size = 18,
  className = '',
}: {
  kind: SealKind;
  size?: number;
  className?: string;
}) {
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.25,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    className,
    'aria-hidden': true,
    focusable: false,
  };

  switch (kind) {
    // A complete impression: ring, notched rim, solid centre.
    case 'filled':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8.25" />
          <circle cx="12" cy="12" r="5" fill="currentColor" stroke="none" />
          <path d="M12 2.6v1.6M12 19.8v1.6M2.6 12h1.6M19.8 12h1.6" />
        </svg>
      );

    // Parsed but never matched: the rim is there, the centre was never struck.
    case 'open':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8.25" />
          <circle cx="12" cy="12" r="4.25" strokeDasharray="1.5 2.2" />
        </svg>
      );

    // Half an impression: struck on one side only.
    case 'half':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8.25" />
          <path d="M12 7.75a4.25 4.25 0 0 1 0 8.5z" fill="currentColor" stroke="none" />
          <path d="M12 6.5v11" />
        </svg>
      );

    // Broken seal: the wax cracked and the halves have shifted apart.
    case 'broken':
      return (
        <svg {...common}>
          <path d="M11 3.9a8.25 8.25 0 0 0-1.6 15.9" />
          <path d="M13.4 4.2A8.25 8.25 0 0 1 14 20" />
          <path d="M12.6 2.4 9.9 8.6l4 2.4-3.2 5.4 1.6 5.2" />
        </svg>
      );

    // A marker whose reference is not there: the rule runs out into nothing.
    case 'dangling':
      return (
        <svg {...common}>
          <path d="M3 7.5h13.5" />
          <path d="M16.5 7.5v6" />
          <path d="M16.5 16.2v.1" strokeWidth={2} />
          <path d="M3 12h7" strokeDasharray="2 2.4" />
        </svg>
      );

    // Supports: a full opening quotation mark.
    case 'quote':
      return (
        <svg {...common}>
          <path
            d="M9.6 6.4c-3 1.2-4.6 3.4-4.6 6.4v4.8h5.4v-5.4H7.2c0-2 .8-3.4 2.4-4.2z"
            fill="currentColor"
            stroke="none"
          />
          <path
            d="M18.6 6.4c-3 1.2-4.6 3.4-4.6 6.4v4.8h5.4v-5.4h-3.2c0-2 .8-3.4 2.4-4.2z"
            fill="currentColor"
            stroke="none"
          />
        </svg>
      );

    // Partially supports / does not address: one mark filled, one hollow.
    case 'quote-half':
      return (
        <svg {...common}>
          <path
            d="M9.6 6.4c-3 1.2-4.6 3.4-4.6 6.4v4.8h5.4v-5.4H7.2c0-2 .8-3.4 2.4-4.2z"
            fill="currentColor"
            stroke="none"
          />
          <path d="M18.6 6.4c-3 1.2-4.6 3.4-4.6 6.4v4.8h5.4v-5.4h-3.2c0-2 .8-3.4 2.4-4.2z" />
        </svg>
      );

    // Contradicts: the quotation mark inverted — the evidence points the other way.
    case 'quote-inverted':
      return (
        <svg {...common}>
          <g transform="rotate(180 12 12)">
            <path
              d="M9.6 6.4c-3 1.2-4.6 3.4-4.6 6.4v4.8h5.4v-5.4H7.2c0-2 .8-3.4 2.4-4.2z"
              fill="currentColor"
              stroke="none"
            />
            <path
              d="M18.6 6.4c-3 1.2-4.6 3.4-4.6 6.4v4.8h5.4v-5.4h-3.2c0-2 .8-3.4 2.4-4.2z"
              fill="currentColor"
              stroke="none"
            />
          </g>
        </svg>
      );

    // No abstract: an empty frame. There is nothing inside to read.
    case 'frame':
      return (
        <svg {...common}>
          <rect x="4.2" y="4.2" width="15.6" height="15.6" rx="0.5" />
          <path d="M7 7.2h10M7 16.8h10" strokeDasharray="1.5 2.4" />
        </svg>
      );
  }
}
