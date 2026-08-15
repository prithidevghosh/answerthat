import { Seal } from './Seal';
import { INK_TEXT, type StatusDescriptor } from '@/lib/status';

/**
 * A status, rendered the only way this app renders one: seal + text label + ink.
 *
 * Components take a StatusDescriptor rather than a tier string, so there is no
 * route to putting a colour on screen without the label that explains it.
 */
export function StatusBadge({
  status,
  size = 'base',
  className = '',
}: {
  status: StatusDescriptor;
  size?: 'base' | 'small';
  className?: string;
}) {
  const small = size === 'small';
  return (
    <span
      className={`inline-flex items-center gap-2 font-ui ${
        small ? 'text-2xs' : 'text-xs'
      } ${INK_TEXT[status.ink]} ${className}`}
    >
      <Seal kind={status.seal} size={small ? 14 : 17} className="shrink-0" />
      <span className="font-medium tracking-[0.01em]">{status.label}</span>
    </span>
  );
}
