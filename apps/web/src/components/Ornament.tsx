/**
 * The plates, and the small engraved marks derived from them.
 *
 * design-system.md §1: the engravings are not wallpaper and not decoration
 * dropped behind a UI. Each one is a composition with a deliberate empty field,
 * and the interface sits in that field. Two placements, and only two:
 *
 *  - <Frontispiece/>  the threshold. The wide plate, full-bleed, full strength,
 *                     horizon low so its open centre fills the upper screen.
 *  - <MarginPlate/>   the working screens. One of the four vertical plates held
 *                     in the outer margin of the leaf, with the text block set
 *                     in the open field to its right — which is the composition
 *                     the vertical plates were drawn for.
 *
 * Both are aria-hidden and pointer-events-none: the plates carry no meaning and
 * intercept no clicks.
 */

/**
 * The open field of the wide plate. Luminance was sampled on a 12x7 grid; this
 * region sits at the same value as the paper and is the only place text may go
 * on the artwork (design-system.md §4).
 *
 * Exported so the threshold screen positions its content against the same
 * numbers this component uses, rather than a hand-tuned guess that drifts.
 */
export const OPEN_FIELD = { left: 0.25, right: 0.72, top: 0, bottom: 0.48 } as const;

/** The four vertical plates, one per working stage. */
export type PlateNumber = 1 | 2 | 3 | 4;

export function Frontispiece() {
  return (
    <div
      aria-hidden="true"
      className="frontispiece pointer-events-none absolute inset-0 select-none"
    />
  );
}

/**
 * The margin vignette on a working leaf.
 *
 * Fixed to the outer edge and held there while the text scrolls past, the way a
 * printed margin ornament stays put on the page. It is safe to fix it because
 * the text never crosses it: `.leaf` pads the working surface by `--margin-col`,
 * which is the same variable this column takes its width from.
 *
 * Two column widths — 236px from 1024px, and clamp(300px, 25vw, 440px) from
 * 1240px — and below 1024px the plate moves to <MarginFoot/> rather than
 * disappearing. **There is no viewport at which a working screen carries none of
 * the art.** An earlier version hid it below 1240px and dimmed it to 0.62 where
 * it did show, which left the working screens looking like a different product
 * than the threshold. Restraint is not the same thing as absence.
 */
export function MarginPlate({ plate }: { plate: PlateNumber }) {
  return (
    <div
      aria-hidden="true"
      data-plate={plate}
      // Width comes from --margin-col, the same variable `.leaf` pads by, so
      // the column and the space reserved for it are one decision.
      className="margin-plate plate-art pointer-events-none fixed inset-y-0 left-0 hidden w-[var(--margin-col)] select-none opacity-[0.88] min-[1024px]:block"
    />
  );
}

/**
 * The same plate at the foot of the document, for viewports too narrow to give
 * up a margin column.
 *
 * This exists because the alternative was hiding the art entirely, and that is
 * what made the working screens read as belonging to a different product than
 * the threshold. A researcher on a 1200px window was getting a page with no
 * engraving anywhere on it.
 *
 * In normal flow, after the content — never fixed behind it — so no text can
 * cross it however long the page runs.
 */
export function MarginFoot({ plate }: { plate: PlateNumber }) {
  return (
    <div
      aria-hidden="true"
      data-plate={plate}
      className="margin-foot plate-art pointer-events-none relative mt-16 h-[34vh] max-h-[320px] w-full select-none opacity-[0.85] min-[1024px]:hidden"
    />
  );
}

/**
 * A plate numeral — "PL. II" — set in engraved caps.
 *
 * The four working stages are the four plates of the suite, so they are
 * numbered like plates rather than labelled like a wizard. The numeral is
 * decorative shorthand beside a real text label, never a replacement for one.
 */
const ROMAN: Record<PlateNumber, string> = { 1: 'I', 2: 'II', 3: 'III', 4: 'IV' };

export function PlateNumeral({
  n,
  className = '',
}: {
  n: PlateNumber;
  className?: string;
}) {
  return (
    <span aria-hidden="true" className={`engraved-label tabular-nums ${className}`}>
      Pl.&nbsp;{ROMAN[n]}
    </span>
  );
}

/**
 * A hairline divider with a centred fleuron — §4 "rules as structure".
 * Section dividers are this, never a grey bar.
 */
export function RuleWithFleuron({ className = '' }: { className?: string }) {
  return (
    <div className={`flex items-center gap-4 ${className}`} aria-hidden="true">
      <span className="h-px flex-1 bg-[var(--rule-hair)]" />
      <Fleuron className="shrink-0 text-cobalt/45" />
      <span className="h-px flex-1 bg-[var(--rule-hair)]" />
    </div>
  );
}

/**
 * The double rule that opens a section: one weighted line, one hairline, the
 * pair the engravers used to close a plate border. Cheaper than a fleuron and
 * it carries the period without costuming the page.
 */
export function DoubleRule({ className = '' }: { className?: string }) {
  return (
    <span aria-hidden="true" className={`block ${className}`}>
      <span className="block h-px bg-[var(--rule-fine)]" />
      <span className="mt-[3px] block h-px bg-[var(--rule-hair)]" />
    </span>
  );
}

/**
 * A printer's fleuron — the aldus leaf, flanked by two curls.
 *
 * Drawn at a 4:1 aspect and set with a solid centre lozenge so it survives at
 * small sizes; the earlier version was all hairline strokes at 16px and
 * collapsed into an illegible mark on screen. `size` is the height.
 */
export function Fleuron({ className = '', size = 14 }: { className?: string; size?: number }) {
  return (
    <svg
      width={size * 4}
      height={size}
      viewBox="0 0 56 14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.1"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      {/* The two leaves, curling in toward the centre. */}
      <path d="M4 7c4.4 0 6.4-3.4 10-3.4 2.4 0 3.8 1.5 3.8 3.4s-1.4 3.4-3.8 3.4C10.4 10.4 8.4 7 4 7z" />
      <path d="M52 7c-4.4 0-6.4-3.4-10-3.4-2.4 0-3.8 1.5-3.8 3.4s1.4 3.4 3.8 3.4C45.6 10.4 47.6 7 52 7z" />
      {/* The centre lozenge, filled so it holds at 14px. */}
      <path d="M28 2.4 31.8 7 28 11.6 24.2 7z" fill="currentColor" stroke="none" />
      {/* Hairlines joining leaf to lozenge. */}
      <path d="M18.2 7h5.2M32.6 7h5.2" />
    </svg>
  );
}

/**
 * Corner fleurons, for the few cards that carry a whole screen.
 *
 * Deliberately NOT the default on every card any more. At forty findings the
 * old treatment put a hundred and sixty little marks on one screen, which is
 * noise pretending to be craft. Cards now take their character from a single
 * status rule down the outer edge (see Plate.tsx); these are reserved for the
 * one or two places a card is the subject rather than an item in a list.
 */
export function CornerFleurons({ className = '' }: { className?: string }) {
  return (
    <>
      <CornerMark className={`absolute left-0 top-0 ${className}`} />
      <CornerMark className={`absolute bottom-0 right-0 rotate-180 ${className}`} />
    </>
  );
}

function CornerMark({ className = '' }: { className?: string }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1"
      aria-hidden="true"
      focusable="false"
      className={`pointer-events-none opacity-40 ${className}`}
    >
      <path d="M0.5 6.5V2.8C0.5 1.5 1.5 0.5 2.8 0.5H6.5" />
      <path d="M3.4 3.4c1.8 0 3.2 1.4 3.2 3.2" />
      <circle cx="3.4" cy="3.4" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  );
}
