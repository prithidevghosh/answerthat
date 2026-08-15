/**
 * Copies packages/csl-styles/*.csl into public/csl/ before every dev run and
 * build.
 *
 * HR-4 requires that citation.js in the browser and Pandoc on the backend read
 * the *same* .csl files. The shared package is the single source of truth; this
 * script produces a build artifact of it rather than a second copy that can
 * drift. It runs on predev/prebuild, so a style change by B1 is picked up
 * without anyone remembering to do anything.
 *
 * It also writes styles.json with a sha256 per file, so preview/export drift is
 * detectable rather than theoretical.
 */
import { createHash } from 'node:crypto';
import { copyFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(here, '../../../packages/csl-styles');
const DEST = resolve(here, '../public/csl');

let files;
try {
  files = readdirSync(SRC).filter((f) => f.endsWith('.csl'));
} catch {
  console.error(
    `\n[csl] Cannot read ${SRC}.\n` +
      `[csl] packages/csl-styles is owned by B1 and is required for HR-4:\n` +
      `[csl] every citation must render through a real .csl file.\n` +
      `[csl] The web app will not render citations without it.\n`,
  );
  process.exit(1);
}

if (files.length === 0) {
  console.error(`\n[csl] ${SRC} contains no .csl files. Refusing to build.\n`);
  process.exit(1);
}

mkdirSync(DEST, { recursive: true });

const manifest = {};
for (const f of files) {
  const src = join(SRC, f);
  copyFileSync(src, join(DEST, f));
  manifest[f.replace(/\.csl$/, '')] = {
    file: `/csl/${f}`,
    sha256: createHash('sha256').update(readFileSync(src)).digest('hex').slice(0, 16),
  };
}

writeFileSync(join(DEST, 'styles.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(`[csl] synced ${files.length} styles from packages/csl-styles`);
