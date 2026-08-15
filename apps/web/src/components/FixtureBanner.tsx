import { USING_FIXTURES } from '@/lib/api/client';

/**
 * Fixture mode is never silent.
 *
 * An app whose entire premise is honest reporting must not be able to show
 * invented findings that look real. When NEXT_PUBLIC_USE_FIXTURES=1 this sits
 * at the top of every screen and says so.
 */
export function FixtureBanner() {
  if (!USING_FIXTURES) return null;
  return (
    <div className="relative z-20 border-b border-sepia/30 bg-sepia/[0.07] px-6 py-2 text-center">
      <p className="font-ui text-2xs text-sepia">
        <strong className="font-semibold">Fixture mode.</strong> Every reference, finding and quote
        on screen is sample data, not a real review.
      </p>
    </div>
  );
}
