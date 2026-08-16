import { CornerFleurons } from './Ornament';
import { INK_RULE, type Ink } from '@/lib/status';

/**
 * A card — a leaf laid on the paper.
 *
 * Square corners, a hairline, and nothing heavier than the §4 shadow ceiling,
 * because engravings do not have drop shadows. The one piece of ornament that
 * earns its place at scale is the rule down the outer edge: a solid 2px ink in
 * the card's status colour, which is how a reader picks the two madder cards
 * out of forty at a glance without reading a word.
 *
 * Failure states get the same treatment as success states — the same leaf, the
 * same spacing, the same rule, a different ink. That is the whole point
 * (§7 rule 8). `fleurons` is opt-in and reserved for a card that is the subject
 * of a screen rather than an item in a list.
 */
export function Plate({
  children,
  accent,
  className = '',
  as: Tag = 'div',
  fleurons = false,
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
      className={`relative border border-hair bg-leaf shadow-plate ${
        accent ? 'pl-[2px]' : ''
      } ${className}`}
      {...rest}
    >
      {accent && (
        <span
          aria-hidden="true"
          className={`absolute inset-y-0 left-0 w-[2px] ${INK_RULE[accent]}`}
        />
      )}
      {fleurons && <CornerFleurons className="text-cobalt" />}
      {children}
    </Tag>
  );
}
