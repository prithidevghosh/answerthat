/**
 * Typed fixtures — development only, behind NEXT_PUBLIC_USE_FIXTURES=1.
 *
 * These exist so the five screens can be built and reviewed before B3's
 * endpoints land, and so every state has something to render. They are built to
 * the shapes in ./types and ../contracts, nothing else.
 *
 * They deliberately over-represent failure: unparseable references, unresolved
 * references, an orphan marker, a missing abstract, a contradiction, a
 * zero-result search, a kernel rejection, and an anchor that could not be
 * reattached. Those states are the product, so they are what we design against.
 *
 * Fixture mode is announced in the UI at all times (see <FixtureBanner/>). A
 * fabricated review that looks like a real one is precisely what HR-3 forbids.
 */
import type {
  CslJson,
  DocumentIR,
  Finding,
  OrphanMarker,
  ParsedReference,
  SourceRecord,
} from '../contracts';
import type {
  CommandResult,
  ExportManifest,
  OrphanOption,
  ParseResult,
  StructuralDiff,
  TierCounts,
} from './types';

const csl = (
  id: string,
  title: string,
  authors: [string, string][],
  year: number,
  container: string,
  extra: Partial<CslJson> = {},
): CslJson => ({
  id,
  type: 'article-journal',
  title,
  author: authors.map(([family, given]) => ({ family, given })),
  issued: { 'date-parts': [[year]] },
  'container-title': container,
  ...extra,
});

// ---------------------------------------------------------------------------
// Sources — each carries provenance proving it came from an HTTP response.
// ---------------------------------------------------------------------------
const prov = (
  provider: 'semantic_scholar' | 'openalex' | 'crossref',
  endpoint: string,
  external_url: string,
) => ({ provider, endpoint, retrieved_at: '2026-08-15T09:12:44Z', external_url });

export const SOURCES: Record<string, SourceRecord> = {
  's2:204e3073870fae3d05bcbc2f6a8e263d9b72e776': {
    source_id: 's2:204e3073870fae3d05bcbc2f6a8e263d9b72e776',
    csl: csl(
      's2:204e3073870fae3d05bcbc2f6a8e263d9b72e776',
      'Attention Is All You Need',
      [
        ['Vaswani', 'Ashish'],
        ['Shazeer', 'Noam'],
        ['Parmar', 'Niki'],
      ],
      2017,
      'Advances in Neural Information Processing Systems',
      { DOI: '10.48550/arXiv.1706.03762', page: '5998-6008' },
    ),
    provenance: prov(
      'semantic_scholar',
      '/graph/v1/paper/search/match',
      'https://www.semanticscholar.org/paper/204e3073870fae3d05bcbc2f6a8e263d9b72e776',
    ),
    abstract:
      'The dominant sequence transduction models are based on complex recurrent or convolutional neural networks that include an encoder and a decoder. The best performing models also connect the encoder and decoder through an attention mechanism. We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely.',
    abstract_source: 's2',
  },
  'openalex:W2963403868': {
    source_id: 'openalex:W2963403868',
    csl: csl(
      'openalex:W2963403868',
      'Longformer: The Long-Document Transformer',
      [
        ['Beltagy', 'Iz'],
        ['Peters', 'Matthew E.'],
        ['Cohan', 'Arman'],
      ],
      2020,
      'arXiv',
      { DOI: '10.48550/arXiv.2004.05150' },
    ),
    provenance: prov('openalex', '/works', 'https://openalex.org/W2963403868'),
    abstract:
      'Transformer-based models are unable to process long sequences due to their self-attention operation, which scales quadratically with the sequence length. To address this limitation, we introduce the Longformer with an attention mechanism that scales linearly with sequence length, making it easy to process documents of thousands of tokens or longer.',
    abstract_source: 'openalex_inverted',
  },
  'openalex:W3099711166': {
    source_id: 'openalex:W3099711166',
    csl: csl(
      'openalex:W3099711166',
      'Efficient Transformers: A Survey',
      [
        ['Tay', 'Yi'],
        ['Dehghani', 'Mostafa'],
        ['Bahri', 'Dara'],
      ],
      2022,
      'ACM Computing Surveys',
      { DOI: '10.1145/3530811', volume: '55', issue: '6', page: '1-28' },
    ),
    provenance: prov('crossref', '/works/10.1145/3530811', 'https://doi.org/10.1145/3530811'),
    abstract:
      'Transformer model architectures have garnered immense interest lately due to their effectiveness across a range of domains like language, vision and reinforcement learning. In the field of natural language processing for example, Transformers have become an indispensable staple in the modern deep learning stack. Recently, a dizzying number of "X-former" models have been proposed.',
    abstract_source: 's2',
  },
  's2:9f8a1c2b7e4d6a0b3c5e8f1a2d4b6c8e0f2a4b6c': {
    source_id: 's2:9f8a1c2b7e4d6a0b3c5e8f1a2d4b6c8e0f2a4b6c',
    csl: csl(
      's2:9f8a1c2b7e4d6a0b3c5e8f1a2d4b6c8e0f2a4b6c',
      'Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks',
      [
        ['Lewis', 'Patrick'],
        ['Perez', 'Ethan'],
        ['Piktus', 'Aleksandra'],
      ],
      2020,
      'Advances in Neural Information Processing Systems',
      { DOI: '10.48550/arXiv.2005.11401' },
    ),
    provenance: prov(
      'semantic_scholar',
      '/graph/v1/paper/batch',
      'https://www.semanticscholar.org/paper/9f8a1c2b7e4d6a0b3c5e8f1a2d4b6c8e0f2a4b6c',
    ),
    // The fallback chain ran to the end and came back empty. This is a real,
    // displayable outcome — not an error to swallow.
    abstract: null,
    abstract_source: 'unavailable',
  },
  'openalex:W4285119': {
    source_id: 'openalex:W4285119',
    csl: csl(
      'openalex:W4285119',
      'Sparse Attention Does Not Reliably Reduce Wall-Clock Latency',
      [
        ['Okafor', 'Chidinma'],
        ['Lindqvist', 'Bo'],
      ],
      2024,
      'Transactions of the Association for Computational Linguistics',
      { DOI: '10.1162/tacl_a_00644', volume: '12', page: '881-899' },
    ),
    provenance: prov('openalex', '/works', 'https://openalex.org/W4285119'),
    abstract:
      'Sparse attention mechanisms are widely reported to reduce the asymptotic cost of long-context inference. We benchmark eleven published sparse-attention kernels across three accelerator families and find that measured wall-clock latency improves in only four of thirty-three configurations, and regresses in twelve. We conclude that asymptotic gains do not transfer to deployed systems without kernel-level co-design.',
    abstract_source: 's2',
  },
};

// ---------------------------------------------------------------------------
// Parsed references — all five tiers, with the raw string always retained.
// ---------------------------------------------------------------------------
const resolvedSeeds: [string, string, [string, string][], number, string, string][] = [
  [
    's2:204e3073870fae3d05bcbc2f6a8e263d9b72e776',
    'Attention Is All You Need',
    [
      ['Vaswani', 'Ashish'],
      ['Shazeer', 'Noam'],
      ['Parmar', 'Niki'],
    ],
    2017,
    'Advances in Neural Information Processing Systems',
    'A. Vaswani, N. Shazeer, N. Parmar, et al., "Attention is all you need," in Advances in Neural Information Processing Systems, 2017, pp. 5998-6008.',
  ],
  [
    'openalex:W2963403868',
    'Longformer: The Long-Document Transformer',
    [
      ['Beltagy', 'Iz'],
      ['Peters', 'Matthew E.'],
      ['Cohan', 'Arman'],
    ],
    2020,
    'arXiv',
    'I. Beltagy, M. E. Peters, and A. Cohan, "Longformer: The long-document transformer," arXiv:2004.05150, 2020.',
  ],
  [
    'openalex:W3099711166',
    'Efficient Transformers: A Survey',
    [
      ['Tay', 'Yi'],
      ['Dehghani', 'Mostafa'],
      ['Bahri', 'Dara'],
    ],
    2022,
    'ACM Computing Surveys',
    'Y. Tay, M. Dehghani, D. Bahri, and D. Metzler, "Efficient transformers: A survey," ACM Comput. Surv., vol. 55, no. 6, pp. 1-28, 2022.',
  ],
  [
    's2:9f8a1c2b7e4d6a0b3c5e8f1a2d4b6c8e0f2a4b6c',
    'Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks',
    [
      ['Lewis', 'Patrick'],
      ['Perez', 'Ethan'],
      ['Piktus', 'Aleksandra'],
    ],
    2020,
    'Advances in Neural Information Processing Systems',
    'P. Lewis, E. Perez, A. Piktus, et al., "Retrieval-augmented generation for knowledge-intensive NLP tasks," in NeurIPS, 2020.',
  ],
];

const filler: [string, [string, string][], number, string][] = [
  ['Big Bird: Transformers for Longer Sequences', [['Zaheer', 'Manzil']], 2020, 'NeurIPS'],
  ['Reformer: The Efficient Transformer', [['Kitaev', 'Nikita']], 2020, 'ICLR'],
  ['Linformer: Self-Attention with Linear Complexity', [['Wang', 'Sinong']], 2020, 'arXiv'],
  ['Rethinking Attention with Performers', [['Choromanski', 'Krzysztof']], 2021, 'ICLR'],
  ['FlashAttention: Fast and Memory-Efficient Exact Attention', [['Dao', 'Tri']], 2022, 'NeurIPS'],
  ['Train Short, Test Long: Attention with Linear Biases', [['Press', 'Ofir']], 2022, 'ICLR'],
  ['Scaling Laws for Neural Language Models', [['Kaplan', 'Jared']], 2020, 'arXiv'],
  ['Dense Passage Retrieval for Open-Domain QA', [['Karpukhin', 'Vladimir']], 2020, 'EMNLP'],
  ['REALM: Retrieval-Augmented Language Model Pre-Training', [['Guu', 'Kelvin']], 2020, 'ICML'],
  ['Sparse is Enough in Scaling Transformers', [['Jaszczur', 'Sebastian']], 2021, 'NeurIPS'],
  ['Memorizing Transformers', [['Wu', 'Yuhuai']], 2022, 'ICLR'],
  ['Unlimiformer: Long-Range Transformers with Unlimited Input', [['Bertsch', 'Amanda']], 2023, 'NeurIPS'],
  ['Lost in the Middle: How Language Models Use Long Contexts', [['Liu', 'Nelson F.']], 2024, 'TACL'],
  ['RoFormer: Enhanced Transformer with Rotary Position Embedding', [['Su', 'Jianlin']], 2024, 'Neurocomputing'],
  ['Mamba: Linear-Time Sequence Modeling with Selective State Spaces', [['Gu', 'Albert']], 2024, 'COLM'],
  ['Ring Attention with Blockwise Transformers', [['Liu', 'Hao']], 2024, 'ICLR'],
  ['LongNet: Scaling Transformers to 1,000,000,000 Tokens', [['Ding', 'Jiayu']], 2023, 'arXiv'],
  ['Landmark Attention: Random-Access Infinite Context', [['Mohtashami', 'Amirkeivan']], 2023, 'NeurIPS'],
  ['Efficient Streaming Language Models with Attention Sinks', [['Xiao', 'Guangxuan']], 2024, 'ICLR'],
  ['YaRN: Efficient Context Window Extension', [['Peng', 'Bowen']], 2024, 'ICLR'],
  ['In-Context Retrieval-Augmented Language Models', [['Ram', 'Ori']], 2023, 'TACL'],
  ['Query Rewriting for Retrieval-Augmented LLMs', [['Ma', 'Xinbei']], 2023, 'EMNLP'],
  ['Precise Zero-Shot Dense Retrieval without Relevance Labels', [['Gao', 'Luyu']], 2023, 'ACL'],
  ['ColBERT: Efficient and Effective Passage Search', [['Khattab', 'Omar']], 2020, 'SIGIR'],
  ['Sentence-BERT: Sentence Embeddings using Siamese Networks', [['Reimers', 'Nils']], 2019, 'EMNLP'],
  ['SPECTER: Document-level Representation Learning', [['Cohan', 'Arman']], 2020, 'ACL'],
  ['Beyond the Imitation Game', [['Srivastava', 'Aarohi']], 2023, 'TMLR'],
  ['Holistic Evaluation of Language Models', [['Liang', 'Percy']], 2023, 'TMLR'],
  ['The Pile: An 800GB Dataset of Diverse Text', [['Gao', 'Leo']], 2020, 'arXiv'],
  ['Chinchilla: Training Compute-Optimal LLMs', [['Hoffmann', 'Jordan']], 2022, 'NeurIPS'],
  ['LLaMA: Open and Efficient Foundation Language Models', [['Touvron', 'Hugo']], 2023, 'arXiv'],
  ['Mixture-of-Experts with Expert Choice Routing', [['Zhou', 'Yanqi']], 2022, 'NeurIPS'],
  ['GLaM: Efficient Scaling of Language Models', [['Du', 'Nan']], 2022, 'ICML'],
  ['Switch Transformers: Scaling to Trillion Parameter Models', [['Fedus', 'William']], 2022, 'JMLR'],
];

function ieeeRaw(title: string, a: [string, string][], year: number, venue: string, n: number) {
  const [family, given] = a[0];
  return `[${n}] ${given[0]}. ${family} et al., "${title.toLowerCase()}," in ${venue}, ${year}.`;
}

const resolved: ParsedReference[] = [
  ...resolvedSeeds.map(([sourceId, title, authors, year, container, raw], i) => ({
    ref_id: `b${i}`,
    raw_string: raw,
    csl: csl(sourceId, title, authors, year, container),
    tier: 'resolved' as const,
    parse_confidence: 0.97,
    agreement_score: [0.98, 0.94, 0.99, 0.91][i],
    source_id: sourceId,
  })),
  ...filler.map(([title, authors, year, venue], i) => {
    const n = i + resolvedSeeds.length;
    return {
      ref_id: `b${n}`,
      raw_string: ieeeRaw(title, authors, year, venue, n + 1),
      csl: csl(`openalex:W${5000000 + i}`, title, authors, year, venue),
      tier: 'resolved' as const,
      parse_confidence: 0.9 + ((i * 7) % 9) / 100,
      agreement_score: 0.86 + ((i * 11) % 13) / 100,
      source_id: `openalex:W${5000000 + i}`,
    };
  }),
];

const parsedUnresolved: ParsedReference[] = [
  {
    ref_id: 'b38',
    raw_string:
      'M. Ferreira and K. Oyelaran, "Adaptive routing under bounded memory," Tech. Rep. TR-2019-114, Institute for Applied Computation, 2019.',
    csl: csl(
      'local:b38',
      'Adaptive routing under bounded memory',
      [
        ['Ferreira', 'M.'],
        ['Oyelaran', 'K.'],
      ],
      2019,
      'Institute for Applied Computation',
      { type: 'report' },
    ),
    tier: 'parsed_unresolved',
    parse_confidence: 0.91,
    agreement_score: null,
    source_id: null,
  },
  {
    ref_id: 'b39',
    raw_string:
      'H. Nakamura, "Notes on sparse routing" (unpublished manuscript), 2021.',
    csl: csl('local:b39', 'Notes on sparse routing', [['Nakamura', 'H.']], 2021, '', {
      type: 'manuscript',
    }),
    tier: 'parsed_unresolved',
    parse_confidence: 0.88,
    agreement_score: null,
    source_id: null,
  },
  {
    ref_id: 'b40',
    raw_string:
      'Working Group on Retrieval Benchmarks, "Interim recommendations," 2nd ed., 2022.',
    csl: csl('local:b40', 'Interim recommendations', [], 2022, '', { type: 'report' }),
    tier: 'parsed_unresolved',
    parse_confidence: 0.83,
    agreement_score: null,
    source_id: null,
  },
  {
    ref_id: 'b41',
    raw_string: 'D. Whitmore, Long-Context Systems, 2nd ed. Cambridge: Ridgeway Press, 2023.',
    csl: csl('local:b41', 'Long-Context Systems', [['Whitmore', 'D.']], 2023, 'Ridgeway Press', {
      type: 'book',
    }),
    tier: 'parsed_unresolved',
    parse_confidence: 0.94,
    agreement_score: null,
    source_id: null,
  },
];

const lowConfidence: ParsedReference[] = [
  {
    ref_id: 'b42',
    raw_string:
      'S. Ali, R. Gupta, "Hierarchical caches for attention" IEEE Trans. Par. Dist. Sys. 34(7) 2023 1122-1139.',
    csl: csl(
      'openalex:W6100001',
      'Hierarchical caches for attention',
      [
        ['Ali', 'S.'],
        ['Gupta', 'R.'],
      ],
      2023,
      'IEEE Transactions on Parallel and Distributed Systems',
      { volume: '34', issue: '7', page: '1122-1139' },
    ),
    tier: 'low_confidence',
    parse_confidence: 0.61,
    agreement_score: 0.79,
    source_id: null,
  },
  {
    ref_id: 'b43',
    raw_string: 'Petrova & Lindgren (2020) Streaming inference. JMLR 21.',
    csl: csl(
      'openalex:W6100002',
      'Streaming inference',
      [
        ['Petrova', ''],
        ['Lindgren', ''],
      ],
      2020,
      'Journal of Machine Learning Research',
      { volume: '21' },
    ),
    tier: 'low_confidence',
    parse_confidence: 0.54,
    agreement_score: 0.81,
    source_id: null,
  },
  {
    ref_id: 'b44',
    raw_string: 'Chen et al. Sparse kernels. 2021.',
    csl: csl('openalex:W6100003', 'Sparse kernels', [['Chen', '']], 2021, ''),
    tier: 'low_confidence',
    parse_confidence: 0.47,
    agreement_score: 0.76,
    source_id: null,
  },
];

const quarantined: ParsedReference[] = [
  {
    ref_id: 'b45',
    // Rendered verbatim, in mono, never truncated. This is evidence.
    raw_string:
      '[46] J. Ha ̈rtel, ‘‘On the con- vergence of block- sparse rout- ing,’’ in Proc. Int. Conf. on Mach. Learn. (ICML), vol. 139, 2021, p. 4021–4033. [Online]. Available: http://proceedings.mlr.press/v139/ha ̈rtel21a.html (accessed Jan. 4, 2 0 2 4).',
    csl: null,
    tier: 'quarantined',
    parse_confidence: 0.12,
    agreement_score: null,
    source_id: null,
  },
  {
    ref_id: 'b46',
    raw_string:
      '47 Kowalski,A.;Ãbrahám,É.;—— ibid. 12(3),pp.55‑71,doi:10.1000/xyz123 [see also ref. 12 above]',
    csl: null,
    tier: 'quarantined',
    parse_confidence: 0.08,
    agreement_score: null,
    source_id: null,
  },
];

export const REFERENCES: ParsedReference[] = [
  ...resolved,
  ...parsedUnresolved,
  ...lowConfidence,
  ...quarantined,
];

const ORPHAN_SNIPPET =
  'Earlier work on block-sparse routing [48] reported comparable throughput, though under a different memory budget.';

export const ORPHAN_MARKERS: OrphanMarker[] = [
  {
    // Keyed by anchor_id, not ref_id: an orphan marker is defined by having no reference.
    anchor_id: 'a-orphan-1',
    marker_text: '[48]',
    span_id: 'sp-5-2',
    section_id: 'sec-5',
    section_title: '5. Discussion',
    target: null,
    reason: "target 'b47' not in listBibl",
    page: 8,
    snippet: ORPHAN_SNIPPET,
  },
];

export const COUNTS: TierCounts = {
  resolved: resolved.length,
  parsed_unresolved: parsedUnresolved.length,
  low_confidence: lowConfidence.length,
  quarantined: quarantined.length,
  orphan_marker: ORPHAN_MARKERS.length,
  total_detected:
    resolved.length + parsedUnresolved.length + lowConfidence.length + quarantined.length,
};

// ---------------------------------------------------------------------------
// Document IR
// ---------------------------------------------------------------------------
const span = (id: string, text: string, anchors: [string, string[], number][] = []) => ({
  id,
  text,
  citation_anchors: anchors.map(([anchor_id, source_ids, offset_in_span]) => ({
    anchor_id,
    source_ids,
    offset_in_span,
    original_marker_text: null,
    provenance_kind: 'parsed' as const,
    confidence: 1,
  })),
});

export const DOCUMENT: DocumentIR = {
  doc_id: 'doc-fixture-1',
  version: 3,
  metadata: {
    title: 'Sparse Attention Routing for Long-Context Scientific Retrieval',
    style_id: 'ieee',
    style_confidence: 0.91,
    style_ambiguous: false,
  },
  quarantine: [
    { raw: quarantined[0].raw_string, reason: 'parse_failed', page: 11 },
    { raw: quarantined[1].raw_string, reason: 'parse_failed', page: 11 },
    { raw: ORPHAN_SNIPPET, reason: 'orphan_marker', page: 8 },
  ],
  sections: [
    {
      id: 'sec-0',
      level: 1,
      title: 'Abstract',
      order: 0,
      blocks: [
        {
          id: 'bl-0-0',
          type: 'paragraph',
          order: 0,
          placeholder_caption: null,
          spans: [
            span(
              'sp-0-0',
              'We introduce a routing scheme that selects sparse attention patterns per query, reducing long-context retrieval cost without loss of recall.',
            ),
          ],
        },
      ],
    },
    {
      id: 'sec-1',
      level: 1,
      title: '1. Introduction',
      order: 1,
      blocks: [
        {
          id: 'bl-1-0',
          type: 'paragraph',
          order: 0,
          placeholder_caption: null,
          spans: [
            span(
              'sp-1-0',
              'Transformer attention scales quadratically with sequence length, which has motivated a long line of sparse and linear approximations.',
              [['a-1', ['s2:204e3073870fae3d05bcbc2f6a8e263d9b72e776'], 121]],
            ),
            span(
              'sp-1-1',
              'Sparse attention reduces the asymptotic cost of long-context inference from quadratic to linear in sequence length.',
              [['a-2', ['openalex:W2963403868'], 110]],
            ),
          ],
        },
      ],
    },
    {
      id: 'sec-2',
      level: 1,
      title: '2. Related Work',
      order: 2,
      blocks: [
        {
          id: 'bl-2-0',
          type: 'paragraph',
          order: 0,
          placeholder_caption: null,
          spans: [
            span(
              'sp-2-0',
              'Surveys of efficient transformer variants group these methods by the structure they impose on the attention matrix.',
              [['a-3', ['openalex:W3099711166'], 104]],
            ),
          ],
        },
        {
          id: 'bl-2-1',
          type: 'figure',
          order: 1,
          spans: [],
          placeholder_caption:
            'Figure 1: Taxonomy of sparse attention patterns by locality and granularity.',
        },
      ],
    },
    {
      id: 'sec-3',
      level: 1,
      title: '3. Method',
      order: 3,
      blocks: [
        {
          id: 'bl-3-0',
          type: 'paragraph',
          order: 0,
          placeholder_caption: null,
          spans: [
            span(
              'sp-3-0',
              'Our router predicts, for each query block, which key blocks to attend to, using a learned scoring function trained with a retrieval objective.',
            ),
          ],
        },
        {
          id: 'bl-3-1',
          type: 'equation',
          order: 1,
          spans: [],
          placeholder_caption: 'Equation 3: Block routing score s(q, k) = σ(w⊤[q; k] + b).',
        },
      ],
    },
    {
      id: 'sec-4',
      level: 1,
      title: '4. Results',
      order: 4,
      blocks: [
        {
          id: 'bl-4-0',
          type: 'paragraph',
          order: 0,
          placeholder_caption: null,
          spans: [
            span(
              'sp-4-0',
              'Routing reduces measured wall-clock latency by 38% at 32k context while retaining 99.1% of dense recall.',
            ),
          ],
        },
        {
          id: 'bl-4-1',
          type: 'table',
          order: 1,
          spans: [],
          placeholder_caption: 'Table 2: Latency and recall across context lengths.',
        },
      ],
    },
    {
      id: 'sec-5',
      level: 1,
      title: '5. Discussion',
      order: 5,
      blocks: [
        {
          id: 'bl-5-0',
          type: 'paragraph',
          order: 0,
          placeholder_caption: null,
          spans: [
            span(
              'sp-5-0',
              'Retrieval-augmented pipelines benefit most, since the retrieved context dominates the sequence budget.',
              [['a-4', ['s2:9f8a1c2b7e4d6a0b3c5e8f1a2d4b6c8e0f2a4b6c'], 96]],
            ),
            span('sp-5-2', ORPHAN_SNIPPET),
          ],
        },
      ],
    },
  ],
};

export const PARSE_RESULT: ParseResult = {
  document: DOCUMENT,
  references: REFERENCES,
  orphan_markers: ORPHAN_MARKERS,
  counts: COUNTS,
  style: {
    style_id: 'ieee',
    score: 0.91,
    ambiguous: false,
    candidates: [
      { style_id: 'ieee', score: 0.91 },
      { style_id: 'acm-sig-proceedings', score: 0.78 },
      { style_id: 'vancouver', score: 0.74 },
      { style_id: 'nature', score: 0.66 },
      { style_id: 'apa', score: 0.41 },
      { style_id: 'chicago-author-date', score: 0.38 },
    ],
  },
};

// ---------------------------------------------------------------------------
// Findings — ordered by citability descending, covering every label.
// ---------------------------------------------------------------------------
const claim = (claim_id: string, text: string, span_id: string, citability: number, anchors: string[] = []) => ({
  claim_id,
  text,
  span_id,
  anchor_ids: anchors,
  citability,
});

export const FINDINGS: Finding[] = [
  {
    finding_id: 'f-1',
    kind: 'claim_citation_mismatch',
    claim: claim(
      'c-1',
      'Sparse attention reduces the asymptotic cost of long-context inference from quadratic to linear in sequence length.',
      'sp-1-1',
      0.94,
      ['a-2'],
    ),
    source_id: 'openalex:W4285119',
    verification: {
      label: 'contradicts',
      quote:
        'measured wall-clock latency improves in only four of thirty-three configurations, and regresses in twelve',
      abstract_source: 's2',
      confidence: 0.88,
    },
    severity: 'high',
  },
  {
    finding_id: 'f-2',
    kind: 'missing_work',
    claim: claim(
      'c-2',
      'Transformer attention scales quadratically with sequence length, which has motivated a long line of sparse and linear approximations.',
      'sp-1-0',
      0.91,
      ['a-1'],
    ),
    source_id: 'openalex:W2963403868',
    verification: {
      label: 'supports',
      quote:
        'their self-attention operation, which scales quadratically with the sequence length',
      abstract_source: 'openalex_inverted',
      confidence: 0.95,
    },
    severity: 'medium',
  },
  {
    finding_id: 'f-3',
    kind: 'missing_work',
    claim: claim(
      'c-3',
      'Surveys of efficient transformer variants group these methods by the structure they impose on the attention matrix.',
      'sp-2-0',
      0.84,
      ['a-3'],
    ),
    source_id: 'openalex:W3099711166',
    verification: {
      label: 'partially_supports',
      quote: 'a dizzying number of "X-former" models have been proposed',
      abstract_source: 's2',
      confidence: 0.71,
    },
    severity: 'medium',
  },
  {
    finding_id: 'f-4',
    kind: 'missing_work',
    claim: claim(
      'c-4',
      'Retrieval-augmented pipelines benefit most, since the retrieved context dominates the sequence budget.',
      'sp-5-0',
      0.77,
      ['a-4'],
    ),
    source_id: 's2:9f8a1c2b7e4d6a0b3c5e8f1a2d4b6c8e0f2a4b6c',
    verification: {
      // The fallback chain ran out. We say so; we do not guess.
      label: 'unverifiable_no_abstract',
      quote: null,
      abstract_source: 'unavailable',
      confidence: 0,
    },
    severity: 'info',
  },
  {
    finding_id: 'f-5',
    kind: 'missing_work',
    claim: claim(
      'c-5',
      'Routing reduces measured wall-clock latency by 38% at 32k context while retaining 99.1% of dense recall.',
      'sp-4-0',
      0.69,
    ),
    source_id: 's2:204e3073870fae3d05bcbc2f6a8e263d9b72e776',
    verification: {
      label: 'does_not_address',
      quote:
        'We propose a new simple network architecture, the Transformer, based solely on attention mechanisms',
      abstract_source: 's2',
      confidence: 0.64,
    },
    severity: 'low',
  },
  {
    finding_id: 'f-6',
    // Distinct from "no findings yet" — the search ran and returned nothing.
    kind: 'no_candidates_found',
    claim: claim(
      'c-6',
      'Our router predicts, for each query block, which key blocks to attend to, using a learned scoring function trained with a retrieval objective.',
      'sp-3-0',
      0.52,
    ),
    source_id: null,
    verification: null,
    severity: 'info',
  },
];

export const REVIEW_TOTAL = 47;

// ---------------------------------------------------------------------------
// Edit console
// ---------------------------------------------------------------------------
export const ORPHAN_OPTION: OrphanOption = {
  anchor_id: 'a-3',
  marker: '[3]',
  source_ids: ['openalex:W3099711166'],
  fingerprint_id: 'fp-a-3',
  best_span_id: 'sp-2-0',
  best_span_text: 'Efficient-transformer surveys organise methods by attention structure.',
  score: 0.68,
  threshold: 0.82,
  flag_floor: 0.6,
  actions: ['keep', 'move', 'remove'],
};

const SHORTEN_BEFORE =
  'Surveys of efficient transformer variants group these methods by the structure they impose on the attention matrix.';
const SHORTEN_AFTER = 'Efficient-transformer surveys organise methods by attention structure.';
const CITED_BEFORE =
  'Routing reduces measured wall-clock latency by 38% at 32k context while retaining 99.1% of dense recall.';
const CITED_AFTER =
  'Routing reduces measured wall-clock latency by 38% at 32k context while retaining 99.1% of dense recall [49].';

/** The diff for the Shorten: one sentence compressed, one anchor left homeless. */
const SHORTEN_DIFF: StructuralDiff = {
  doc_id: DOCUMENT.doc_id,
  base_version: DOCUMENT.version,
  citations: {
    preserved: true,
    total_before: 12,
    total_after: 12,
    sources_lost: {},
    sources_gained: {},
    held_for_decision: ['a-3'],
    anchors: [
      {
        anchor_id: 'a-3',
        status: 'held_for_decision',
        marker: '[3]',
        before_span_id: 'sp-2-0',
        after_span_id: null,
        source_ids_before: ['openalex:W3099711166'],
        source_ids_after: ['openalex:W3099711166'],
        note: 'Best candidate scored 0.68, below the 0.82 reattachment threshold.',
      },
    ],
  },
  blocks: [
    {
      status: 'modified',
      block_id: 'blk-2-0',
      before_section_id: 'sec-2',
      after_section_id: 'sec-2',
      spans: [
        {
          status: 'modified',
          span_id: 'sp-2-0',
          before_text: SHORTEN_BEFORE,
          after_text: SHORTEN_AFTER,
          anchor_ids: ['a-3'],
        },
      ],
    },
  ],
};

/** The diff for the AddCitations: same sentence, one anchor gained. */
const ADD_CITATIONS_DIFF: StructuralDiff = {
  doc_id: DOCUMENT.doc_id,
  base_version: DOCUMENT.version,
  citations: {
    preserved: true,
    total_before: 12,
    total_after: 13,
    sources_lost: {},
    sources_gained: { 'openalex:W4285119': 1 },
    held_for_decision: [],
    anchors: [
      {
        anchor_id: 'a-49',
        status: 'added',
        marker: '[49]',
        before_span_id: null,
        after_span_id: 'sp-4-0',
        source_ids_before: [],
        source_ids_after: ['openalex:W4285119'],
        note: null,
      },
    ],
  },
  blocks: [
    {
      status: 'modified',
      block_id: 'blk-4-0',
      before_section_id: 'sec-4',
      after_section_id: 'sec-4',
      spans: [
        {
          status: 'modified',
          span_id: 'sp-4-0',
          before_text: CITED_BEFORE,
          after_text: CITED_AFTER,
          anchor_ids: ['a-49'],
        },
      ],
    },
  ],
};

export const COMMAND_RESULT: CommandResult = {
  change_set_id: 'cs-7f2a1c93',
  doc_id: DOCUMENT.doc_id,
  base_version: DOCUMENT.version,
  command: 'Shorten the related work section and back the latency claim with a citation.',
  plan_id: 'plan-7f2a',
  status: 'awaiting_approval',
  attempts: 2,
  changes: [
    {
      change: {
        change_id: 'ch-1',
        op: {
          op: 'Shorten',
          target_ids: ['sp-2-0'],
          params: { ratio: 0.7 },
          no_typed_op_applies: false,
          justification: null,
        },
        new_fragment: { replace_spans: [{ id: 'sp-2-0', text: SHORTEN_AFTER }] },
        new_source_ids: [],
        orphaned_anchor_ids: ['a-3'],
        rationale:
          'Compressed one sentence by 38%. The anchor for [3] could not be reattached above the 0.82 threshold and is raised for your decision.',
      },
      verdict: {
        decision: 'flag',
        reasons: ['Anchor a-3 found no home above the reattachment threshold after the transform.'],
        flags: ['orphaned_anchor'],
      },
      diff: SHORTEN_DIFF,
      notes: [],
      orphans: [ORPHAN_OPTION],
    },
    {
      change: {
        change_id: 'ch-2',
        op: {
          op: 'AddCitations',
          target_ids: ['sp-4-0'],
          params: { count: 1 },
          no_typed_op_applies: false,
          justification: null,
        },
        new_fragment: { replace_spans: [{ id: 'sp-4-0', text: CITED_AFTER }] },
        new_source_ids: ['openalex:W4285119'],
        orphaned_anchor_ids: [],
        rationale:
          'Added a citation to Okafor & Lindqvist (2024), which reports the wall-clock benchmark this claim depends on.',
      },
      verdict: {
        decision: 'flag',
        reasons: ['The cited source contradicts rather than supports the host claim.'],
        flags: ['weak_verification'],
      },
      diff: ADD_CITATIONS_DIFF,
      notes: [],
      orphans: [],
    },
  ],
  rejected: [
    {
      operation: {
        op: 'RewriteSection',
        target_ids: ['sec-2'],
        params: { instruction: 'Reframe the related work around routing rather than sparsity.' },
        no_typed_op_applies: false,
        justification: null,
      },
      reasons: [
        'The document source_id multiset would shrink by 2 without an approved removal operation (HR-5).',
        'A newly asserted claim carried no anchor with a supporting verification.',
      ],
      attempt: 2,
    },
  ],
  message: '1 operation(s) could not be validated after 2 retries.',
};

export const EXPORT_MANIFEST: ExportManifest = {
  doc_id: DOCUMENT.doc_id,
  version: DOCUMENT.version,
  filename: 'sparse-attention-routing.revised.tex',
  placeholder_blocks: [
    { type: 'figure', count: 1 },
    { type: 'table', count: 1 },
    { type: 'equation', count: 1 },
  ],
  bibliography_entries: COUNTS.resolved,
  style_id: 'ieee',
  style_uncertain: false,
  exportable: true,
  blocked_reason: null,
};
