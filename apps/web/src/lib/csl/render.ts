/**
 * HR-4 — the only place a citation becomes a string.
 *
 * Every citation and bibliography entry in this app renders through
 * citation.js reading a real .csl file from packages/csl-styles, synced into
 * public/csl by scripts/sync-csl-styles.mjs. Pandoc renders the export from
 * those same files, which is what keeps preview and export from drifting.
 *
 * There is no template string in this module and there must never be one
 * anywhere else. If a citation cannot be rendered, we say so — we do not fall
 * back to assembling one by hand, because a hand-assembled citation is exactly
 * the failure HR-4 exists to make impossible.
 */
import { Cite, plugins } from '@citation-js/core';
import '@citation-js/plugin-csl';
import type { CslJson } from '../contracts';

const DEFAULT_LOCALE = 'en-US';

type StyleId = string;

const styleCache = new Map<StyleId, Promise<void>>();
let localeLoaded: Promise<void> | null = null;

async function fetchText(url: string, what: string): Promise<string> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new CitationRenderError(
      `Could not load ${what} (${res.status}). Citations cannot be rendered without it.`,
    );
  }
  return res.text();
}

export class CitationRenderError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CitationRenderError';
  }
}

async function ensureLocale(): Promise<void> {
  if (!localeLoaded) {
    localeLoaded = (async () => {
      const xml = await fetchText(`/csl/locales-${DEFAULT_LOCALE}.xml`, 'the CSL locale');
      const config = plugins.config.get('@csl');
      config.locales.add(DEFAULT_LOCALE, xml);
    })().catch((err) => {
      localeLoaded = null; // let a later attempt retry rather than caching failure
      throw err;
    });
  }
  return localeLoaded;
}

/**
 * Registers a style with citation.js by reading the actual .csl XML.
 * Styles are fetched once and cached for the session.
 */
export async function ensureStyle(styleId: StyleId): Promise<void> {
  await ensureLocale();
  let pending = styleCache.get(styleId);
  if (!pending) {
    pending = (async () => {
      const xml = await fetchText(`/csl/${styleId}.csl`, `the ${styleId} citation style`);
      const config = plugins.config.get('@csl');
      config.templates.add(styleId, xml);
    })().catch((err) => {
      styleCache.delete(styleId);
      throw err;
    });
    styleCache.set(styleId, pending);
  }
  return pending;
}

function toCite(csl: CslJson): Cite {
  // citation.js mutates what it is given; hand it a copy, and guarantee the id
  // it needs for cluster rendering without touching the caller's object.
  const entry = { ...csl, id: csl.id ?? csl.DOI ?? 'entry' };
  return new Cite([entry]);
}

/**
 * Renders one bibliography entry as HTML, through the real style file.
 * Throws CitationRenderError — callers surface it, never swallow it (HR-3).
 */
export async function renderBibliographyEntry(
  csl: CslJson,
  styleId: StyleId,
): Promise<string> {
  await ensureStyle(styleId);
  try {
    return toCite(csl)
      .format('bibliography', {
        format: 'html',
        template: styleId,
        lang: DEFAULT_LOCALE,
      })
      .trim();
  } catch (err) {
    throw new CitationRenderError(
      `The ${styleId} style could not render this record: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }
}

/** Renders a whole bibliography in one pass, so citeproc can disambiguate. */
export async function renderBibliography(
  entries: CslJson[],
  styleId: StyleId,
): Promise<string> {
  await ensureStyle(styleId);
  const cite = new Cite(
    entries.map((e, i) => ({ ...e, id: e.id ?? e.DOI ?? `entry-${i}` })),
  );
  try {
    return cite
      .format('bibliography', { format: 'html', template: styleId, lang: DEFAULT_LOCALE })
      .trim();
  } catch (err) {
    throw new CitationRenderError(
      `The ${styleId} style could not render the bibliography: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }
}

/**
 * Renders an in-text citation ("[12]" or "(Vaswani et al., 2017)") according to
 * the style's own in-text rules. Used for anchor seals in the edit console.
 */
export async function renderInText(csl: CslJson, styleId: StyleId): Promise<string> {
  await ensureStyle(styleId);
  try {
    return toCite(csl)
      .format('citation', { format: 'text', template: styleId, lang: DEFAULT_LOCALE })
      .trim();
  } catch (err) {
    throw new CitationRenderError(
      `The ${styleId} style could not render an in-text citation: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }
}

/** A short human label for a source — author + year, taken from CSL fields. */
export function shortLabel(csl: CslJson): string {
  const first = csl.author?.[0];
  const name = first?.family ?? first?.literal ?? null;
  const year = csl.issued?.['date-parts']?.[0]?.[0] ?? null;
  if (name && year) return `${name} ${year}`;
  if (name) return name;
  if (year) return String(year);
  return 'Untitled record';
}
