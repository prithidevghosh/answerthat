'use client';

import { useMemo } from 'react';

/**
 * Word-level diff between the before and after text of a change.
 *
 * Deliberately small and local: this only ever renders one sentence or
 * paragraph against another, so an O(n*m) LCS over words is both fast enough
 * and easier to trust than pulling in a diff library.
 *
 * Removed text is madder strikethrough, added text is verdigris, per
 * design-system.md §5.
 */
type Piece = { kind: 'same' | 'added' | 'removed'; text: string };

function tokenize(s: string): string[] {
  // Keep whitespace attached to the preceding token so reassembly is exact.
  return s.match(/\S+\s*/g) ?? [];
}

function diffWords(before: string, after: string): Piece[] {
  const a = tokenize(before);
  const b = tokenize(after);

  // LCS table
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array<number>(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i].trim() === b[j].trim() ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const out: Piece[] = [];
  const push = (kind: Piece['kind'], text: string) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind) last.text += text;
    else out.push({ kind, text });
  };

  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i].trim() === b[j].trim()) {
      push('same', a[i]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      push('removed', a[i]);
      i++;
    } else {
      push('added', b[j]);
      j++;
    }
  }
  while (i < m) push('removed', a[i++]);
  while (j < n) push('added', b[j++]);

  return out;
}

export function DiffText({ before, after }: { before: string; after: string }) {
  const pieces = useMemo(() => diffWords(before, after), [before, after]);

  return (
    <p className="measure text-base leading-relaxed text-primary">
      {pieces.map((p, idx) => {
        if (p.kind === 'same') return <span key={idx}>{p.text}</span>;
        if (p.kind === 'removed') {
          return (
            <del
              key={idx}
              className="bg-madder/[0.07] text-madder decoration-madder/60 decoration-1"
            >
              {p.text}
            </del>
          );
        }
        return (
          <ins
            key={idx}
            className="bg-verdigris/[0.08] text-verdigris no-underline"
          >
            {p.text}
          </ins>
        );
      })}
    </p>
  );
}

/**
 * The same word diff, laid out as two columns: the paragraph the user wrote on
 * the left, the paragraph we propose on the right.
 *
 * The interleaved form above reads as one mangled sentence — deletions and
 * insertions in sequence, so neither the original nor the replacement can be
 * read as a sentence. This is the review surface for "did the model rewrite my
 * paper into something I don't recognise", and answering that means reading
 * both versions as prose, not decoding one merged strip. Each side still carries
 * the word-level highlight, so *what* moved is visible without hunting.
 *
 * Columns at `sm` and up; stacked, still labelled, below that — two 40-character
 * columns on a phone would be worse than one readable one.
 */
export function SideBySideDiff({ before, after }: { before: string; after: string }) {
  const pieces = useMemo(() => diffWords(before, after), [before, after]);

  return (
    <div className="grid gap-px overflow-hidden rounded border border-hair bg-hair sm:grid-cols-2">
      <DiffColumn
        label="Original"
        // `before` is the user's own text. If a change adds a whole new sentence
        // there is no original, and an empty column with a label says that more
        // honestly than collapsing the column and leaving the reader to infer it.
        empty="Nothing here before — this text is new."
        text={before}
      >
        {pieces.map((p, idx) =>
          p.kind === 'added' ? null : p.kind === 'removed' ? (
            <del
              key={idx}
              className="bg-madder/[0.07] text-madder decoration-madder/60 decoration-1"
            >
              {p.text}
            </del>
          ) : (
            <span key={idx}>{p.text}</span>
          ),
        )}
      </DiffColumn>

      <DiffColumn
        label="Revised"
        empty="Nothing here after — this text would be removed."
        text={after}
      >
        {pieces.map((p, idx) =>
          p.kind === 'removed' ? null : p.kind === 'added' ? (
            <ins key={idx} className="bg-verdigris/[0.08] text-verdigris no-underline">
              {p.text}
            </ins>
          ) : (
            <span key={idx}>{p.text}</span>
          ),
        )}
      </DiffColumn>
    </div>
  );
}

function DiffColumn({
  label,
  text,
  empty,
  children,
}: {
  label: string;
  text: string;
  empty: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-paper-deep/50 px-5 py-4">
      <p className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">{label}</p>
      {text.trim() === '' ? (
        <p className="mt-2 text-xs italic leading-relaxed text-muted">{empty}</p>
      ) : (
        <p className="mt-2 text-base leading-relaxed text-primary">{children}</p>
      )}
    </div>
  );
}
