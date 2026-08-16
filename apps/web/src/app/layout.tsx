import type { Metadata } from 'next';
import { Bodoni_Moda, Spectral, Archivo, IBM_Plex_Mono } from 'next/font/google';
import './globals.css';

/**
 * design-system.md §3. Loaded through next/font so they are self-hosted and
 * carry no layout shift — no external font CDN in the render path.
 *
 * The pairing is the plates' own logic. Bodoni is a Didone: extreme stroke
 * contrast, unbracketed hairline serifs — the letterform the copperplate
 * engravers were cutting at exactly the moment these scenes depict. It carries
 * the whole period reference, so nothing else has to.
 *
 * Spectral then does the reading. It is a Production Type face, drawn in Paris
 * for long-form text on screen: low contrast, generous x-height, real italics.
 * A researcher reads forty findings on it at 11pm, which is a job Bodoni would
 * do badly and Spectral does well.
 */
const bodoni = Bodoni_Moda({
  subsets: ['latin'],
  variable: '--font-bodoni',
  display: 'swap',
});

const spectral = Spectral({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  style: ['normal', 'italic'],
  variable: '--font-spectral',
  display: 'swap',
});

const archivo = Archivo({
  subsets: ['latin'],
  variable: '--font-archivo',
  display: 'swap',
});

const plexMono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--font-plex-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'Answerthat',
  description:
    'Upload a paper. Receive a peer review grounded in real academic search, and edit by instruction with every citation intact.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${bodoni.variable} ${spectral.variable} ${archivo.variable} ${plexMono.variable}`}
    >
      <body>
        <a href="#main" className="skip-link">
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
