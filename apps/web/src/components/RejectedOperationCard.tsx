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
 * The retry count is shown because it is meaningful: the planner was told why
 * and tried again (max 2, CP-6), and still could not produce something valid.
 */
export function RejectedOperationCard({ rejected }: { rejected: RejectedOperation }) {
  return (
    <Plate as="li" accent="sanguine" className="px-6 py-6 sm:px-8">
      <span className="inline-flex items-center gap-2 font-ui text-xs font-medium text-sanguine">
        <Seal kind="broken" size={17} />
        Refused — this part of your instruction was not applied
      </span>

      <p className="mt-4 font-ui text-2xs uppercase tracking-[0.12em] text-muted">Operation</p>
      <p className="mt-1 text-base text-primary">{rejected.op_summary}</p>

      <p className="mt-5 font-ui text-2xs uppercase tracking-[0.12em] text-muted">
        Why it was refused
      </p>
      <ul className="mt-2 space-y-2">
        {rejected.reasons.map((reason, i) => (
          <li
            key={i}
            className="measure border-l-2 border-sanguine/40 pl-4 text-xs leading-relaxed text-secondary"
          >
            {reason}
          </li>
        ))}
      </ul>

      <p className="mt-5 font-ui text-2xs text-muted">
        The planner was given these reasons and retried {rejected.retries_spent} time
        {rejected.retries_spent === 1 ? '' : 's'} before this was surfaced to you. Nothing in your
        document was changed by this operation.
      </p>
    </Plate>
  );
}
