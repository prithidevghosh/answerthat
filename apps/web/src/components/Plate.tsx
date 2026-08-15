import { CornerFleurons } from './Ornament';
import { INK_BORDER, type Ink } from '@/lib/status';

/**
 * A card. Square corners, one hairline, corner fleurons, no drop shadow beyond
 * the §4 ceiling — engravings do not have drop shadows.
 *
 * `accent` tints the hairline to a status ink. Failure states get the same
 * treatment as success states: the same card, the same fleurons, the same
 * spacing, a different ink. That is the whole point (§7 rule 5).
 */
export function Plate({
  children,
  accent,
  className = '',
  as: Tag = 'div',
  fleurons = true,
  ...rest
}: {
  children: React.ReactNode;
  accent?: Ink;
  className?: string;
  as?: 'div' | 'article' | 'section' | 'li';
  fleurons?: boolean;
} & React.HTMLAttributes<HTMLElement>) {
  return (
    <Tag
      className={`relative rounded border bg-plate shadow-plate ${
        accent ? INK_BORDER[accent] : 'border-hair'
      } ${className}`}
      {...rest}
    >
      {fleurons && <CornerFleurons className="text-indigo" />}
      {children}
    </Tag>
  );
}
