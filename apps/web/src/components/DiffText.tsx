'use client';

import { useMemo } from 'react';

/**
 * Word-level diff between the before and after text of a change.
 *
 * Deliberately small and local: this only ever renders one sentence or
 * paragraph against another, so an O(n*m) LCS over words is both fast enough
 * and easier to trust than pulling in a diff library.
 *
 * Removed text is sanguine strikethrough, added text is verdigris, per
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
              className="bg-sanguine/[0.07] text-sanguine decoration-sanguine/60 decoration-1"
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
