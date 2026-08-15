/**
 * The plate, and the small engraved marks derived from it.
 *
 * design-system.md §1: the plate is the stage, and the content sits in the sky
 * the plate already gives us. It is not cropped to a decorative sliver and it
 * is never dimmed behind a scrim.
 *
 * Two placements, and only two:
 *
 *  - <HeroPlate/>   the threshold. Full-bleed, full strength, horizon low so
 *                   the luminous sky fills the upper screen.
 *  - <HorizonBand/> the working screens. The plate's ground edge at the foot of
 *                   the document, after the content and never behind it,
 *                   fading up into the paper. Calm at the desk.
 *
 * Both are aria-hidden and pointer-events-none: the plate carries no meaning
 * and intercepts no clicks.
 */

/**
 * The measured sky. Luminance was sampled on a 12x7 grid over the plate; the
 * region below holds 0.89-0.94 relative luminance — indistinguishable from
 * --paper — and is the only place text may sit on the artwork
 * (design-system.md §4).
 *
 * Exported so the threshold screen positions its content against the same
 * numbers this component uses, rather than a hand-tuned guess that drifts.
 */
export const SKY = { left: 0.29, right: 0.71, top: 0, bottom: 0.46 } as const;

export function HeroPlate() {
  return (
    <div
      aria-hidden="true"
      className="plate-img plate-full pointer-events-none absolute inset-0 select-none"
    />
  );
}

/**
 * The plate's ground edge, closing the foot of a working screen.
 *
 * Rendered in the document flow AFTER the content, never fixed behind it. A
 * fixed band would put the engraving under scrolling text, which is the one
 * thing §7 rule 2 forbids outright — and it is exactly what the 11pm test is
 * there to catch.
 */
export function HorizonBand({ className = '' }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`plate-img plate-horizon pointer-events-none relative h-[34vh] max-h-[300px] w-full select-none opacity-[0.5] ${className}`}
    />
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
      <Fleuron className="shrink-0 text-indigo/45" />
      <span className="h-px flex-1 bg-[var(--rule-hair)]" />
    </div>
  );
}

export function Fleuron({ className = '', size = 16 }: { className?: string; size?: number }) {
  return (
    <svg
      width={size * 2.2}
      height={size}
      viewBox="0 0 44 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1"
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      <path d="M22 4c-2.6 0-4.4 2.2-4.4 6s1.8 6 4.4 6 4.4-2.2 4.4-6-1.8-6-4.4-6z" />
      <path d="M22 10h-8.5c-2.4 0-3.6-1.2-4.2-3.4M22 10h8.5c2.4 0 3.6-1.2 4.2-3.4" />
      <path d="M13.5 10c-2.4 0-3.6 1.2-4.2 3.4M30.5 10c2.4 0 3.6 1.2 4.2 3.4" />
      <circle cx="22" cy="10" r="1.1" fill="currentColor" stroke="none" />
    </svg>
  );
}

/**
 * Corner fleurons for cards — §4: square corners with a traced fleuron at two
 * opposing corners, rather than a border radius.
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
