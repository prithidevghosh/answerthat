/**
 * design-system.md §2 "Status mapping — memorise this", as data.
 *
 * Every status in this app resolves through this table, so a tier can never be
 * shown as a bare colour. Each entry carries an ink, a text label and a seal
 * icon: colour is the third channel, never the only one (§7 rule 2 / WCAG AA).
 *
 * `note` is the plain-language explanation shown to a researcher who has never
 * read our contracts. It says what happened and what it means for them.
 */
import type { ConfidenceTier, VerificationLabel, AbstractSource } from './contracts';

export type Ink = 'indigo' | 'sepia' | 'madder' | 'verdigris';

export type SealKind =
  | 'filled' //   resolved — a complete impression
  | 'open' //     parsed, not found
  | 'half' //     low confidence
  | 'broken' //   could not parse
  | 'dangling' // marker with no reference
  | 'quote' //    supports
  | 'quote-half' // partially supports
  | 'quote-inverted' // contradicts
  | 'frame'; //   no abstract — an empty frame

export interface StatusDescriptor {
  ink: Ink;
  label: string;
  seal: SealKind;
  note: string;
}

export const TIER_STATUS: Record<ConfidenceTier, StatusDescriptor> = {
  resolved: {
    ink: 'indigo',
    label: 'Resolved',
    seal: 'filled',
    note: 'Matched to a record in an external index. The metadata below comes from that record, not from our parse.',
  },
  parsed_unresolved: {
    ink: 'sepia',
    label: 'Parsed, not found in any index',
    seal: 'open',
    note: 'We read this reference cleanly, but no index we searched holds a matching record. It may be a report, a thesis, or unindexed work.',
  },
  low_confidence: {
    ink: 'sepia',
    label: 'Low confidence — fields uncertain',
    seal: 'half',
    note: 'We are not confident in the fields below, and no external record agreed with them strongly enough to replace them. Check them against the raw string.',
  },
  quarantined: {
    ink: 'madder',
    label: 'Could not parse',
    seal: 'broken',
    note: 'We could not segment this reference into fields at all. The raw string is shown in full, exactly as it appeared in your document.',
  },
  orphan_marker: {
    ink: 'madder',
    label: 'Marker with no reference',
    seal: 'dangling',
    note: 'Your text cites this marker, but no matching entry exists in the bibliography.',
  },
};

export const VERIFICATION_STATUS: Record<VerificationLabel, StatusDescriptor> = {
  supports: {
    ink: 'indigo',
    label: 'Supports',
    seal: 'quote',
    note: 'The quoted passage from the abstract supports this claim.',
  },
  partially_supports: {
    ink: 'sepia',
    label: 'Partially supports',
    seal: 'quote-half',
    note: 'The quoted passage bears on this claim but does not establish all of it.',
  },
  does_not_address: {
    ink: 'sepia',
    label: 'Does not address',
    seal: 'quote-half',
    note: 'The source is real and readable, but its abstract does not speak to this claim.',
  },
  contradicts: {
    ink: 'madder',
    label: 'Contradicts',
    seal: 'quote-inverted',
    note: 'The quoted passage runs against this claim. Worth reading before you rely on it.',
  },
  unverifiable_no_abstract: {
    ink: 'sepia',
    label: 'No abstract available — cannot verify',
    seal: 'frame',
    note: 'No abstract could be retrieved for this source through any of our providers, so we cannot check the claim against it. We are not saying the claim is wrong — we are saying we do not know.',
  },
};

export const ABSTRACT_SOURCE_LABEL: Record<AbstractSource, string> = {
  s2: 'Semantic Scholar abstract',
  openalex_inverted: 'OpenAlex (reconstructed from inverted index)',
  tldr: 'Semantic Scholar TLDR',
  unavailable: 'No abstract available',
};

/** Tailwind text-colour class per ink. Kept here so no component guesses. */
export const INK_TEXT: Record<Ink, string> = {
  indigo: 'text-indigo',
  sepia: 'text-sepia',
  madder: 'text-madder',
  verdigris: 'text-verdigris',
};

/** Hairline rule colour per ink, for card edges and quote blocks. */
export const INK_BORDER: Record<Ink, string> = {
  indigo: 'border-indigo/35',
  sepia: 'border-sepia/40',
  madder: 'border-madder/40',
  verdigris: 'border-verdigris/40',
};

export const INK_BG: Record<Ink, string> = {
  indigo: 'bg-indigo/[0.04]',
  sepia: 'bg-sepia/[0.05]',
  madder: 'bg-madder/[0.05]',
  verdigris: 'bg-verdigris/[0.05]',
};

export const TIER_ORDER: ConfidenceTier[] = [
  'resolved',
  'parsed_unresolved',
  'low_confidence',
  'quarantined',
  'orphan_marker',
];

/** Short labels for the count strip. Full labels live in TIER_STATUS. */
export const TIER_COUNT_LABEL: Record<ConfidenceTier, string> = {
  resolved: 'resolved',
  parsed_unresolved: 'parsed, not found',
  low_confidence: 'low confidence',
  quarantined: 'could not parse',
  orphan_marker: 'orphan marker',
};
