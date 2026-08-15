import { MarginPlates } from '@/components/Ornament';
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
 * So there is no nav, no marketing, no feature grid — one drop target and one
 * line of text in a large empty ivory field, with the engraving at full
 * strength in the margins and nowhere near the centre.
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
      {/* The one screen that carries ornament at full strength. */}
      <MarginPlates strength="full" />

      <main
        id="main"
        className="relative z-10 flex min-h-screen flex-col items-center justify-center px-6 py-24"
      >
        <div className="content-column flex flex-col items-center">
          <h1 className="text-center font-display text-4xl font-normal tracking-[-0.01em] text-primary">
            Answerthat
          </h1>

          <p className="measure mt-6 text-center text-lg leading-relaxed text-secondary">
            Upload a paper. Get a peer review grounded in real academic search — and edit by
            instruction, with every citation intact.
          </p>

          <div className="mt-16 w-full max-w-[560px]">
            <UploadDropTarget />
          </div>
        </div>
      </main>
    </>
  );
}
