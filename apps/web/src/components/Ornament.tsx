/**
 * Ornament — margins only, never behind text.
 *
 * design-system.md §4 makes this a hard constraint, so it is enforced here
 * rather than left to each screen to remember:
 *
 *  - MarginPlates is `fixed` to the viewport edges and sits outside the
 *    1100px content column. It is `hidden xl:block` — below 1280px the margin
 *    ornament is *removed entirely*, not scaled down.
 *  - It is aria-hidden and pointer-events-none: it carries no meaning and can
 *    never intercept a click.
 *  - Nothing in this file may be rendered inside the content column.
 *
 * The plates themselves are derived from the project's engraving: the left and
 * right thirds of it, quantized onto the cobalt ink ramp with the plate's sky
 * snapped to --paper so the bands dissolve into the page. See
 * scripts/build-plates.md for the derivation.
 */

export type PlateStrength = 'full' | 'quiet';

/**
 * @param strength 'full' is the upload threshold — the one screen that carries
 * ornament at full weight. Every working surface after it uses 'quiet'
 * ("ceremony at the threshold, calm at the desk").
 */
export function MarginPlates({ strength = 'quiet' }: { strength?: PlateStrength }) {
  const opacity = strength === 'full' ? 'opacity-100' : 'opacity-[0.28]';

  return (
    <div
      aria-hidden="true"
      // inset-0, not inset-y-0: a fixed box with only vertical insets collapses
      // to zero width, and the right-hand plate then anchors to x=0 and renders
      // off the left edge of the screen.
      className={`pointer-events-none fixed inset-0 z-0 hidden select-none xl:block ${opacity}`}
    >
      <Plate side="left" />
      <Plate side="right" />
    </div>
  );
}

function Plate({ side }: { side: 'left' | 'right' }) {
  // The inner edge fades to nothing and the top/bottom taper, so the plate
  // meets the paper without a seam. Masks are doubled and intersected: one
  // horizontal, one vertical.
  const fadeInward =
    side === 'left'
      ? 'linear-gradient(to right, #000 42%, transparent 100%)'
      : 'linear-gradient(to left, #000 42%, transparent 100%)';
  const fadeEnds = 'linear-gradient(to bottom, transparent 0%, #000 14%, #000 84%, transparent 100%)';

  return (
    <div
      // The background-image pair (webp declaration, then a typed image-set that
      // overrides it where supported) lives in globals.css, because a React
      // style object cannot express the same property twice.
      className={`plate plate--${side} absolute inset-y-0 ${side === 'left' ? 'left-0' : 'right-0'}`}
      style={{
        // Wide enough to read as a plate, never wide enough to reach the
        // content column: the column caps at 1100px and this only renders at
        // >=1280px, so at the tightest case each band still clears it.
        width: 'max(180px, calc((100vw - 1100px) / 2))',
        backgroundPosition: `${side} center`,
        WebkitMaskImage: `${fadeInward}, ${fadeEnds}`,
        maskImage: `${fadeInward}, ${fadeEnds}`,
        WebkitMaskComposite: 'source-in',
        maskComposite: 'intersect',
      }}
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
      <Fleuron className="shrink-0 text-cobalt/45" />
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
