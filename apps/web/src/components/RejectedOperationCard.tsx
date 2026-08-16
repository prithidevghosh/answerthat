import { Plate } from './Plate';
import { Seal } from './Seal';
import type { RejectedOperation } from '@/lib/api/types';

/**
 * An operation the invariant kernel refused.
 *
 * The kernel is pure code (ADR-007), so its rejections are explainable — and
 * HR-3 means they are explained. This card exists because the alternative is an
 * edit that silently did less than the user asked for, which is the worst
 * possible outcome: the user believes their instruction was carried out.
 *
 * The attempt number is shown because it is meaningful: the planner was told why
 * and tried again (max 2, CP-6), and still could not produce something valid.
 */
export function RejectedOperationCard({ rejected }: { rejected: RejectedOperation }) {
  const { operation } = rejected;
  const summary = operation
    ? `${operation.op} — ${operation.target_ids.join(', ')}`
    : // No operation at all: the planner's output was not a plan. Saying so beats
      // printing an empty line where an operation should be.
      'The planner did not return a usable plan';

  return (
    <Plate as="li" accent="madder" className="px-6 py-6 sm:px-8">
      <span className="inline-flex items-center gap-2 font-ui text-xs font-medium text-madder">
        <Seal kind="broken" size={17} />
        Refused — this part of your instruction was not applied
      </span>

      <p className="mt-4 font-ui text-2xs uppercase tracking-[0.12em] text-muted">Operation</p>
      <p className="mt-1 text-base text-primary">{summary}</p>

      <p className="mt-5 font-ui text-2xs uppercase tracking-[0.12em] text-muted">
        Why it was refused
      </p>
      <ul className="mt-2 space-y-2">
        {rejected.reasons.map((reason, i) => (
          <li
            key={i}
            className="measure border-l-2 border-madder/40 pl-4 text-xs leading-relaxed text-secondary"
          >
            {reason}
          </li>
        ))}
      </ul>

      <p className="mt-5 font-ui text-2xs text-muted">
        The planner was given these reasons and this was attempt {rejected.attempt} before it was
        surfaced to you. Nothing in your document was changed by this operation.
      </p>
    </Plate>
  );
}
