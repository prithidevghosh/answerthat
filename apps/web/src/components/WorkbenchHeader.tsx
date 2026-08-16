import Link from 'next/link';
import type { PlateNumber } from './Ornament';

export type Step = 'parse' | 'review' | 'edit' | 'export';

const STEPS: { id: Step; label: string; plate: PlateNumber }[] = [
  { id: 'parse', label: 'Parse', plate: 1 },
  { id: 'review', label: 'Review', plate: 2 },
  { id: 'edit', label: 'Edit', plate: 3 },
  { id: 'export', label: 'Export', plate: 4 },
];

const ROMAN: Record<PlateNumber, string> = { 1: 'I', 2: 'II', 3: 'III', 4: 'IV' };

/**
 * Chrome for the working surfaces.
 *
 * The four stages are numbered as the four plates of the suite, which is not
 * decoration for its own sake: each working screen carries the matching
 * vertical engraving in its margin, so the numeral on the step and the plate on
 * the page are the same fact stated twice. It also reads better than a wizard —
 * a researcher can go back to Pl. I without feeling they have undone something.
 *
 * The numerals are aria-hidden and sit beside a real text label; they never
 * carry the meaning on their own (§7 rule 6).
 */
export function WorkbenchHeader({
  docId,
  current,
  title,
  version,
}: {
  docId: string;
  /**
   * `'chat'` is not a fifth stage and is deliberately not in `STEPS`.
   *
   * The conversational flow is a second path through the same four operations,
   * not a step after export, so it does not get a stage of its own — and the
   * four stages are not shown beside it either. A stepper on the chat screen
   * says the conversation is one phase of a four-phase march, which is the
   * opposite of what the flow is: the agent does all four, in whatever order
   * the user asks for, and none of them is a place the user has to go.
   *
   * What it gets instead is one crossing link, which is all §4 asked for. The
   * guided screens stay readable — same document, same versions — and a user
   * who wants to study a diff on Pl. III arrives there and picks up the stepper
   * from that side.
   */
  current: Step | 'chat';
  title?: string | null;
  version?: number;
}) {
  return (
    // Opaque. A translucent bar over running text smears the lines passing
    // under it — see §4 "no frosted glass". Ink is opaque; so is this.
    <header className="sticky top-0 z-30 border-b border-hair bg-paper">
      <div className="content-column flex flex-wrap items-end justify-between gap-x-8 gap-y-4 py-4">
        <div className="min-w-0 pb-1">
          <Link
            href="/"
            className="font-display text-lg leading-none tracking-[-0.01em] text-primary transition-colors duration-ink hover:text-cobalt"
          >
            Answerthat
          </Link>
          {title && (
            <p className="mt-1.5 truncate font-body text-2xs italic text-muted" title={title}>
              {title}
              {version !== undefined && <> · version {version}</>}
            </p>
          )}
        </div>

        {current === 'chat' ? (
          <Link
            href={`/documents/${docId}/parse`}
            className="group flex items-baseline gap-2 pb-1 text-muted transition-colors duration-ink ease-ink hover:text-cobalt"
          >
            <span className="font-body text-xs leading-none">Guided screens</span>
            <span
              aria-hidden="true"
              className="text-2xs leading-none transition-transform duration-ink ease-ink group-hover:translate-x-0.5"
            >
              →
            </span>
          </Link>
        ) : (
        <nav aria-label="Stages">
          <ol className="flex items-stretch">
            {STEPS.map((step, i) => {
              const isCurrent = step.id === current;
              return (
                <li key={step.id} className="flex items-stretch">
                  {i > 0 && (
                    <span
                      aria-hidden="true"
                      className="mx-1 self-center text-2xs text-palest sm:mx-2"
                    >
                      ·
                    </span>
                  )}
                  <Link
                    href={`/documents/${docId}/${step.id}`}
                    aria-current={isCurrent ? 'step' : undefined}
                    className={`group flex flex-col items-center gap-1 px-2 pb-1.5 pt-1 transition-colors duration-ink ease-ink sm:px-3 ${
                      isCurrent ? 'text-cobalt' : 'text-muted hover:text-cobalt'
                    }`}
                  >
                    <span
                      aria-hidden="true"
                      className={`engraved-label transition-opacity duration-ink ${
                        isCurrent ? 'opacity-100' : 'opacity-55 group-hover:opacity-80'
                      }`}
                    >
                      Pl.&nbsp;{ROMAN[step.plate]}
                    </span>
                    <span className="font-body text-xs leading-none">{step.label}</span>
                    {/* The rule under the current plate, drawn rather than filled. */}
                    <span
                      aria-hidden="true"
                      className={`h-[2px] w-full origin-left transition-colors duration-ink ${
                        isCurrent ? 'animate-draw-rule bg-cobalt' : 'bg-transparent'
                      }`}
                    />
                  </Link>
                </li>
              );
            })}
          </ol>
        </nav>
        )}
      </div>
    </header>
  );
}
