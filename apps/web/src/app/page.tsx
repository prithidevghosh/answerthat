import { HeroPlate } from '@/components/Ornament';
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
 * So there is no nav, no marketing, no feature grid.
 *
 * design-system.md §5: the plate runs full-bleed at full strength, and the
 * wordmark, one line of text and the drop target sit in the plate's own
 * luminous sky — the measured region at x 29-71%, y 0-46% that holds the same
 * luminance as the paper. The scholars at both edges are turned inward toward
 * exactly that space, waiting to read what gets placed there.
 *
 * Below 900px the sky is too small to hold the wordmark and the target at once,
 * so the plate drops to a foot-of-screen band and the content runs on plain
 * ivory above it (§4 rule 4).
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

      <div className="relative min-h-screen overflow-hidden">
        {/* Narrow viewports: no hero, a band at the foot instead. */}
        <div className="absolute inset-0 hidden min-[900px]:block">
          <HeroPlate />
        </div>
        <div
          aria-hidden="true"
          className="plate-img plate-horizon pointer-events-none absolute inset-x-0 bottom-0 h-[38vh] opacity-70 min-[900px]:hidden"
        />

        <main
          id="main"
          className="relative z-10 flex min-h-screen flex-col items-center px-6 pb-24 pt-[6vh] min-[900px]:pt-[4vh]"
        >
          {/*
            Sits inside the measured sky: the plate's horizon is anchored at 78%
            so the sky occupies roughly the top 45% of the viewport, and this
            column is capped and centred to stay within its 29-71% width.
            No scrim, no panel, no text-shadow — the sky is already at paper
            value, and dimming the engraving to make room for text is forbidden.
          */}
          <div className="flex w-full max-w-[520px] flex-col items-center text-center">
            <h1 className="font-display text-[clamp(2.5rem,5.5vw,3.5rem)] font-normal leading-[1.05] tracking-[-0.015em] text-deepest">
              Answerthat
            </h1>

            <p className="mt-4 max-w-[52ch] font-body text-[clamp(0.95rem,1.25vw,1.125rem)] leading-snug text-secondary">
              Upload a paper. Get a peer review grounded in real academic search — and edit by
              instruction, with every citation intact.
            </p>

            <div className="mt-7 w-full">
              <UploadDropTarget />
            </div>
          </div>
        </main>
      </div>
    </>
  );
}
