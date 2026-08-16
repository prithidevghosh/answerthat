import { Frontispiece, Fleuron, RuleWithFleuron } from '@/components/Ornament';
import { UploadDropTarget } from '@/components/UploadDropTarget';
import { FixtureBanner } from '@/components/FixtureBanner';
import { ConfigurationError } from '@/components/ConfigurationError';
import { getClient } from '@/lib/api/client';

// The status probe must run per request: a server that started before the keys
// were fixed should not keep serving a cached configuration error, or the
// reverse.
export const dynamic = 'force-dynamic';

/**
 * The threshold.
 *
 * goal.md §1: "The first screen is the product: an upload. Not a landing page."
 * So there is no nav, no marketing, no feature grid — one wordmark, one line,
 * one drop target.
 *
 * design-system.md §5: the frontispiece runs full-bleed at full strength and
 * the interface sits in the open centre the plate already gives us — the
 * measured region at x 25-72%, y 0-48% that holds the same luminance as the
 * paper. The scholars at both edges are turned inward toward exactly that
 * space, waiting to read what gets placed there.
 *
 * The arrangement is an 18th-century title page, because that is the one layout
 * this composition is already shaped like: standing head, title, rule, statement
 * of contents, imprint. It costs no extra ornament, and it is the difference
 * between art parked behind a form and a page that was actually set.
 *
 * Below 900px the open centre is too small to hold the wordmark and the target
 * at once, so the plate drops to a foot-of-screen band and the content runs on
 * plain ivory above it (§4 rule 4).
 */
export default async function UploadPage() {
  const status = await getClient().getStatus();

  if (status.kind !== 'ok') {
    return (
      <>
        <FixtureBanner />
        <ConfigurationError status={status} />
      </>
    );
  }

  return (
    <>
      <FixtureBanner />

      <div className="plate-mark relative min-h-screen overflow-hidden">
        {/* Narrow viewports: no frontispiece, a band at the foot instead. */}
        <div className="absolute inset-0 hidden min-[900px]:block">
          <Frontispiece />
        </div>
        <div
          aria-hidden="true"
          className="frontispiece frontispiece-band pointer-events-none absolute inset-x-0 bottom-0 h-[40vh] opacity-80 min-[900px]:hidden"
        />

        <main id="main" className="relative z-10 min-h-screen px-6">
          {/*
            The block is CENTRED IN THE OPEN FIELD, not pinned to the top of it.

            The field is the upper half of the screen (see below), so centring
            inside a 50vh region gives balanced air above and below the title
            page and lets the engraving hold the lower half whole. Pinned to the
            top it read as crammed, with all the slack dumped in one gap.

            The vertical sizes are in `vh` on purpose. With `cover` on a viewport
            narrower in aspect than the plate, the open field is a fixed
            *fraction* of viewport height, so a block built from fixed pixels
            outgrows it on short screens — it did, running to 59% at 1440x900
            and putting the last line over the treeline, where the darkest
            pixels behind it measured 1.07:1.

            THE BUDGET: measured on the plate, the centre column holds 7.46:1
            for `--text-secondary` down to 50% of height, then falls off a cliff
            (3.10:1 at 50-55%, 1.37:1 by 65%). 50% is hard. If you change a size
            here, re-measure — every element must end above it.
          */}
          {/* Centred in the field on both layouts, only the field differs: the
              upper half of the plate on desktop, the ivory above the foot band
              (which is 40vh) on narrow screens. Top-aligned, mobile left a dead
              350px gap between the caption and the band. */}
          {/* `pb-8` used to apply here on every layout and quietly broke the
              centring: with `justify-center`, bottom padding lifts the content
              by half of it, which left just 12px of air above the wordmark at
              1366x768. It is a mobile-only concern, so it is mobile-only now. */}
          <div className="flex min-h-[58vh] flex-col items-center justify-center pb-8 pt-[6vh] min-[900px]:min-h-[50vh] min-[900px]:pb-0 min-[900px]:pt-0">
            {/*
              Four elements, not five. The standing head ("Grounded peer
              review") was cut rather than squeezed: the line beneath it already
              says "a peer review grounded in real academic search", so it was
              the same claim twice, and it was costing the stack the air the
              rest of it needed. Title / rule / statement of contents / imprint
              is the canonical title page anyway.

              The gaps below are the point of that cut — spend the room on
              spacing, not on another line. Fitting the block above the 50%
              cliff by tightening the gaps is the wrong trade; if this ever
              stops fitting again, remove something else rather than crushing
              what is left.
            */}
            <div className="flex w-full max-w-[540px] flex-col items-center text-center">
              <h1 className="font-display text-[clamp(2.5rem,min(7vw,6.5vh),4.25rem)] font-normal leading-[1.02] tracking-[-0.02em] text-deep">
                Answerthat
              </h1>

              <RuleWithFleuron className="mt-[3.2vh] w-full max-w-[260px] [@media(min-height:940px)]:mt-[3.8vh]" />

              {/* `text-wrap: balance` keeps this from dropping a one-word orphan
                  line ("intact.") under a centred block — it was doing exactly
                  that at 1440px. */}
              {/* `text-wrap: balance` keeps this from dropping a one-word orphan
                  line ("intact.") under a centred block. The measure widens on a
                  short screen so the line sets in two rather than three: on a
                  768px-tall viewport the open field is only 384px and the third
                  line is 26px the block cannot spare. */}
              <p className="mt-[3vh] max-w-[60ch] text-balance font-body text-[clamp(0.9375rem,1.25vw,1.0625rem)] italic leading-[1.55] text-secondary [@media(min-height:860px)]:max-w-[46ch] [@media(min-height:940px)]:mt-[3.4vh]">
                Upload a paper. Receive a peer review grounded in real academic search — and edit by
                instruction, with every citation intact.
              </p>

              <div className="mt-[4vh] flex w-full justify-center [@media(min-height:940px)]:mt-[4.6vh]">
                <UploadDropTarget />
              </div>
            </div>
          </div>
        </main>

        {/* A closing mark at the foot, where a colophon would sit. */}
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 bottom-9 z-10 hidden justify-center text-cobalt/40 min-[900px]:flex"
        >
          <Fleuron size={11} />
        </span>
      </div>
    </>
  );
}
