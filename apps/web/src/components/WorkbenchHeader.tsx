import Link from 'next/link';

export type Step = 'parse' | 'review' | 'edit' | 'export';

const STEPS: { id: Step; label: string }[] = [
  { id: 'parse', label: 'Parse' },
  { id: 'review', label: 'Review' },
  { id: 'edit', label: 'Edit' },
  { id: 'export', label: 'Export' },
];

/**
 * Chrome for the working surfaces. "Calm at the desk": a title, a hairline,
 * and the four steps. No ornament — the plates behind these screens already run
 * at reduced strength.
 */
export function WorkbenchHeader({
  docId,
  current,
  title,
  version,
}: {
  docId: string;
  current: Step;
  title?: string | null;
  version?: number;
}) {
  return (
    <header className="relative z-10 border-b border-hair bg-paper/80 backdrop-blur-[2px]">
      <div className="content-column flex flex-wrap items-center justify-between gap-4 py-4">
        <div className="min-w-0">
          <Link
            href="/"
            className="font-display text-lg text-primary transition-colors duration-ink hover:text-cobalt"
          >
            Answerthat
          </Link>
          {title && (
            <p className="mt-0.5 truncate font-ui text-2xs text-muted" title={title}>
              {title}
              {version !== undefined && <> · version {version}</>}
            </p>
          )}
        </div>

        <nav aria-label="Stages">
          <ol className="flex items-center gap-1">
            {STEPS.map((step, i) => {
              const isCurrent = step.id === current;
              return (
                <li key={step.id} className="flex items-center">
                  {i > 0 && (
                    <span aria-hidden="true" className="mx-1 h-px w-6 bg-[var(--rule-hair)]" />
                  )}
                  <Link
                    href={`/documents/${docId}/${step.id}`}
                    aria-current={isCurrent ? 'step' : undefined}
                    className={`rounded px-3 py-1.5 font-ui text-xs transition-colors duration-ink ease-ink ${
                      isCurrent
                        ? 'bg-cobalt/[0.08] text-cobalt'
                        : 'text-secondary hover:bg-cobalt/[0.04] hover:text-cobalt'
                    }`}
                  >
                    {step.label}
                  </Link>
                </li>
              );
            })}
          </ol>
        </nav>
      </div>
    </header>
  );
}
