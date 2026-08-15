/**
 * Minimal type declarations for citation.js, which ships none.
 *
 * These describe only the surface this app uses, and were written against the
 * runtime API as it actually behaves in @citation-js/core 0.7 (verified:
 * plugins.config.get('@csl') exposes `templates` and `locales` registers, each
 * with .add/.get/.has/.list). Do not widen these speculatively — an inaccurate
 * declaration is worse than none, because it type-checks and then fails.
 */
declare module '@citation-js/core' {
  export interface FormatOptions {
    format?: 'text' | 'html' | 'string';
    /** The id a style was registered under via config.templates.add(). */
    template?: string;
    lang?: string;
    entry?: string | string[];
    [key: string]: unknown;
  }

  export class Cite {
    constructor(data?: unknown, options?: Record<string, unknown>);
    format(type: 'bibliography' | 'citation', options?: FormatOptions): string;
    get(options?: Record<string, unknown>): unknown;
    data: unknown[];
  }

  /** A citation.js register — used for both CSL templates and locales. */
  export interface Register<T> {
    add(key: string, value: T): void;
    set(key: string, value: T): void;
    get(key: string): T;
    has(key: string): boolean;
    remove(key: string): void;
    delete(key: string): void;
    list(): string[];
  }

  export interface CslConfig {
    /** .csl XML, keyed by style id. */
    templates: Register<string>;
    /** CSL locale XML, keyed by language tag. */
    locales: Register<string>;
    engine: unknown;
  }

  export const plugins: {
    config: {
      get(pluginId: '@csl'): CslConfig;
      get(pluginId: string): unknown;
    };
  };
}

declare module '@citation-js/plugin-csl';
