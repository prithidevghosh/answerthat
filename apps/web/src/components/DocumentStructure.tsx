import type { Block, Section } from '@/lib/contracts';

const PLACEHOLDER_TYPES: Block['type'][] = ['figure', 'table', 'equation'];

const PLACEHOLDER_LABEL: Record<string, string> = {
  figure: 'Figure',
  table: 'Table',
  equation: 'Equation',
};

/**
 * The document as we understood it: sections in order, with paragraph counts
 * and anchor counts.
 *
 * Placeholder blocks (figures, tables, equations) are shown as placeholders
 * here, not hidden — ADR-008 is a stated scope cut, and the user meets it at
 * parse time rather than discovering it in the export.
 *
 * Takes `sections`, not the whole IR, because the conversational flow renders
 * the same outline from `get_document_outline` — which can serve the *draft*
 * document published at `tei_to_ir`, before there is a version or a
 * bibliography to build a `DocumentIR` around. It only ever read `.sections`
 * anyway. A second copy of this component that drifted from this one would be a
 * worse outcome than a slightly awkward prop.
 */
export function DocumentStructure({ sections }: { sections: Section[] }) {
  return (
    <nav aria-label="Document structure" className="space-y-8">
      {sections.map((section) => {
        const paragraphs = section.blocks.filter((b) => b.type === 'paragraph');
        const placeholders = section.blocks.filter((b) => PLACEHOLDER_TYPES.includes(b.type));
        const anchors = paragraphs.reduce(
          (n, b) => n + b.spans.reduce((m, s) => m + s.citation_anchors.length, 0),
          0,
        );

        return (
          <div key={section.id}>
            <h3 className="font-display text-lg text-primary">{section.title}</h3>

            <p className="mt-1 font-ui text-2xs text-muted">
              {paragraphs.length} paragraph{paragraphs.length === 1 ? '' : 's'}
              {anchors > 0 && <> · {anchors} citation{anchors === 1 ? '' : 's'}</>}
            </p>

            {placeholders.length > 0 && (
              <ul className="mt-3 space-y-2">
                {placeholders.map((block) => (
                  <li
                    key={block.id}
                    className="border-l-2 border-sepia/40 py-1 pl-3 font-ui text-2xs leading-relaxed text-secondary"
                  >
                    <span className="text-sepia">
                      {PLACEHOLDER_LABEL[block.type]} — placeholder
                    </span>
                    {block.placeholder_caption && (
                      <span className="mt-0.5 block text-muted">{block.placeholder_caption}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </nav>
  );
}
