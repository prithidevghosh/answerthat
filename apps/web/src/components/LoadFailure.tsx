import Link from 'next/link';
import { Plate } from './Plate';
import { Seal } from './Seal';

/**
 * A request that failed, said plainly.
 *
 * Distinct from an empty result and distinct from a configuration error: this
 * screen means we asked and did not get an answer. It never falls back to
 * rendering a blank page, because a blank page reads as "nothing here" (HR-3).
 */
export function LoadFailure({
  what,
  docId,
  detail,
}: {
  what: string;
  docId?: string;
  detail?: string | null;
}) {
  return (
    <main id="main" className="content-column py-24">
      <Plate accent="madder" className="px-8 py-10">
        <div className="measure">
          <span className="inline-flex items-center gap-3 font-ui text-xs font-medium text-madder">
            <Seal kind="broken" size={18} />
            Could not load {what}
          </span>

          <p className="mt-5 text-secondary">
            The request reached the API but did not come back with a usable answer. Your document
            has not been changed.
          </p>

          {detail && (
            <pre className="mt-5 overflow-x-auto whitespace-pre-wrap break-words rounded border border-hair bg-paper-deep px-4 py-3 font-mono text-2xs leading-relaxed text-secondary">
              {detail}
            </pre>
          )}

          <div className="mt-8 flex flex-wrap gap-4">
            {docId && (
              <Link
                href={`/documents/${docId}/parse`}
                className="rounded border border-indigo/40 px-5 py-2.5 font-ui text-xs text-indigo transition-colors duration-ink ease-ink hover:bg-indigo/[0.06]"
              >
                Try again
              </Link>
            )}
            <Link
              href="/"
              className="rounded border border-hair px-5 py-2.5 font-ui text-xs text-secondary transition-colors duration-ink ease-ink hover:border-strong hover:text-primary"
            >
              Start over with a new paper
            </Link>
          </div>
        </div>
      </Plate>
    </main>
  );
}
