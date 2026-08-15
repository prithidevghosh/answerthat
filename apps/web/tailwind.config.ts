import type { Config } from 'tailwindcss';

/**
 * design/design-system.md is the contract. Every token below is a direct
 * transcription of it — values live as CSS custom properties in globals.css so
 * there is exactly one source of truth, and Tailwind reads them through var().
 */
const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        paper: 'var(--paper)',
        'paper-deep': 'var(--paper-deep)',
        plate: 'var(--plate)',

        // Inks — semantic, not decorative.
        cobalt: 'var(--ink-cobalt)',
        'cobalt-lt': 'var(--ink-cobalt-lt)',
        sepia: 'var(--ink-sepia)',
        sanguine: 'var(--ink-sanguine)',
        verdigris: 'var(--ink-verdigris)',

        primary: 'var(--text-primary)',
        secondary: 'var(--text-secondary)',
        muted: 'var(--text-muted)',
      },
      borderColor: {
        hair: 'var(--rule-hair)',
        strong: 'var(--rule-strong)',
        DEFAULT: 'var(--rule-hair)',
      },
      fontFamily: {
        display: 'var(--font-display)',
        body: 'var(--font-body)',
        ui: 'var(--font-ui)',
        mono: 'var(--font-mono)',
      },
      // 1.25 minor third: 12 / 14 / 16 / 20 / 25 / 31 / 39 / 49
      fontSize: {
        '2xs': ['0.75rem', { lineHeight: '1.4' }], //  12
        xs: ['0.875rem', { lineHeight: '1.5' }], //    14
        base: ['1rem', { lineHeight: '1.65' }], //     16 — body
        lg: ['1.25rem', { lineHeight: '1.45' }], //    20
        xl: ['1.5625rem', { lineHeight: '1.35' }], //  25
        '2xl': ['1.9375rem', { lineHeight: '1.25' }], //31
        '3xl': ['2.4375rem', { lineHeight: '1.15' }], //39
        '4xl': ['3.0625rem', { lineHeight: '1.1' }], // 49
      },
      spacing: {
        1: '4px',
        2: '8px',
        3: '12px',
        4: '16px',
        6: '24px',
        8: '32px',
        12: '48px',
        16: '64px',
        24: '96px',
      },
      maxWidth: {
        content: '1100px', // the calm centre column
        measure: '68ch', //  a finding is prose; treat it as prose
      },
      borderRadius: {
        DEFAULT: '2px', // corners are square; fleurons do the ornament
        none: '0',
        full: '9999px',
      },
      boxShadow: {
        // Engravings have no drop shadows. This is the ceiling.
        plate: '0 1px 2px rgba(22,32,46,0.06)',
        none: 'none',
      },
      transitionTimingFunction: {
        ink: 'cubic-bezier(0.2, 0, 0.2, 1)',
      },
      transitionDuration: {
        ink: '180ms',
        'ink-slow': '240ms',
      },
      keyframes: {
        // Findings fade and rise as they stream in — never slide or bounce.
        'rise-in': {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        // The one indulgence, once, at export.
        impress: {
          '0%': { transform: 'scale(1.02)' },
          '100%': { transform: 'scale(1)' },
        },
      },
      animation: {
        'rise-in': 'rise-in 240ms cubic-bezier(0.2, 0, 0.2, 1) both',
        impress: 'impress 400ms cubic-bezier(0.2, 0, 0.2, 1) both',
      },
    },
  },
  plugins: [],
};

export default config;
