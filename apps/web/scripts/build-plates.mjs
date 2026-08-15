// Extracts the left/right margin bands from hero-plate.svg, culls sub-pixel
// detail, and quantizes the full-colour trace onto the cobalt ink ramp.
// Output: two small duotone SVGs that obey "ornament lives in the margins".
import { readFileSync, writeFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const SRC = process.argv[2];
const OUT_DIR = process.argv[3];
const MIN_AREA = Number(process.argv[4] ?? 40);
const STEPS = Number(process.argv[5] ?? 10);

const W = 2912, H = 1632;
const BAND = 700;               // px of source kept at each outer edge
const svg = readFileSync(SRC, 'utf8');

// ---- ink ramp: --paper #F7F3EA -> --ink-cobalt #1B3A6B ----
const PAPER = [0xf7, 0xf3, 0xea];
const COBALT = [0x1b, 0x3a, 0x6b];
const hex = (n) => n.toString(16).padStart(2, '0');
const ramp = [];
for (let i = 0; i < STEPS; i++) {
  const t = i / (STEPS - 1);
  ramp.push('#' + PAPER.map((p, k) => hex(Math.round(p + (COBALT[k] - p) * t))).join(''));
}

function bbox(d) {
  let x = 0, y = 0, sx = 0, sy = 0;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const put = (px, py) => {
    if (px < minX) minX = px; if (px > maxX) maxX = px;
    if (py < minY) minY = py; if (py > maxY) maxY = py;
  };
  const t = d.match(/[a-zA-Z]|-?\d*\.?\d+(?:e[-+]?\d+)?/g) || [];
  let i = 0, cmd = 'M';
  const num = () => parseFloat(t[i++]);
  while (i < t.length) {
    if (/[a-zA-Z]/.test(t[i])) cmd = t[i++];
    const rel = cmd === cmd.toLowerCase(), c = cmd.toUpperCase();
    if (c === 'M' || c === 'L' || c === 'T') {
      const a = num(), b = num();
      x = rel ? x + a : a; y = rel ? y + b : b;
      if (c === 'M') { sx = x; sy = y; } put(x, y);
    } else if (c === 'H') { const a = num(); x = rel ? x + a : a; put(x, y); }
    else if (c === 'V') { const a = num(); y = rel ? y + a : a; put(x, y); }
    else if (c === 'C') {
      const a = num(), b = num(), cc = num(), dd = num(), e = num(), f = num();
      put(rel ? x + a : a, rel ? y + b : b); put(rel ? x + cc : cc, rel ? y + dd : dd);
      x = rel ? x + e : e; y = rel ? y + f : f; put(x, y);
    } else if (c === 'S' || c === 'Q') {
      const a = num(), b = num(), e = num(), f = num();
      put(rel ? x + a : a, rel ? y + b : b);
      x = rel ? x + e : e; y = rel ? y + f : f; put(x, y);
    } else if (c === 'A') {
      num(); num(); num(); num(); num();
      const e = num(), f = num();
      x = rel ? x + e : e; y = rel ? y + f : f; put(x, y);
    } else if (c === 'Z') { x = sx; y = sy; put(x, y); }
    else i++;
  }
  return [minX, minY, maxX, maxY];
}

// perceptual luminance -> ramp index (dark trace ink -> dark cobalt)
function quant(fill) {
  const m = /^#([0-9a-f]{6})$/i.exec(fill);
  if (!m) return ramp[ramp.length - 1];
  const v = parseInt(m[1], 16);
  const r = (v >> 16) & 255, g = (v >> 8) & 255, b = v & 255;
  const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  // Anything at or above PAPER_CUT is the plate's own sky/ground: snap it to the
  // page ivory exactly, so the band dissolves into --paper instead of sitting on
  // it as a grey rectangle.
  const PAPER_CUT = 0.86;
  if (lum >= PAPER_CUT) return ramp[0];
  const t = 1 - lum / PAPER_CUT;                 // rescale remaining range
  const idx = Math.round(t * (STEPS - 1));
  return ramp[Math.max(0, Math.min(STEPS - 1, idx))];
}

// translate a path's start point so the band sits at x=0
function shift(d, dx) {
  const t = d.match(/[a-zA-Z]|-?\d*\.?\d+(?:e[-+]?\d+)?/g) || [];
  // only the first absolute M needs adjusting; everything after is relative
  // in this trace, but handle absolute commands generally.
  let out = '', i = 0, cmd = 'M';
  const flush = (s) => { out += s; };
  while (i < t.length) {
    if (/[a-zA-Z]/.test(t[i])) { cmd = t[i]; flush(t[i++]); continue; }
    const c = cmd.toUpperCase(), rel = cmd === cmd.toLowerCase();
    const take = (n) => t.slice(i, i + n).map(Number);
    if (c === 'M' || c === 'L' || c === 'T') {
      const [a, b] = take(2); i += 2;
      flush(`${rel ? a : a - dx} ${b} `);
    } else if (c === 'H') { const [a] = take(1); i += 1; flush(`${rel ? a : a - dx} `); }
    else if (c === 'V') { const [a] = take(1); i += 1; flush(`${a} `); }
    else if (c === 'C') {
      const p = take(6); i += 6;
      flush(p.map((v, k) => (!rel && k % 2 === 0 ? v - dx : v)).join(' ') + ' ');
    } else if (c === 'S' || c === 'Q') {
      const p = take(4); i += 4;
      flush(p.map((v, k) => (!rel && k % 2 === 0 ? v - dx : v)).join(' ') + ' ');
    } else if (c === 'A') {
      const p = take(7); i += 7;
      flush(p.map((v, k) => (!rel && k === 5 ? v - dx : v)).join(' ') + ' ');
    } else { flush(t[i++] + ' '); }
  }
  return out.replace(/\s+([a-zA-Z])/g, '$1').replace(/\s+/g, ' ').trim();
}

const paths = [...svg.matchAll(/<path\s+fill="([^"]*)"\s+d="([^"]*)"\s*\/>/g)];

function build(side) {
  const x0 = side === 'left' ? 0 : W - BAND;
  const x1 = side === 'left' ? BAND : W;
  const kept = [];
  let dropped = 0, culled = 0;
  for (const [, fill, d] of paths) {
    const [bx0, by0, bx1, by1] = bbox(d);
    if (bx1 < x0 || bx0 > x1) { dropped++; continue; }        // outside band
    const area = (bx1 - bx0) * (by1 - by0);
    // NB: never cull by "too large" — the trace relies on painter order, with a
    // dark base rect overpainted by a huge ivory ground. Dropping either leaves
    // flat dark slabs across the foliage.
    if (area < MIN_AREA) { culled++; continue; }               // sub-pixel speck
    kept.push(`<path fill="${quant(fill)}" d="${shift(d, x0)}"/>`);
  }
  const body =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${BAND} ${H}" ` +
    `width="${BAND}" height="${H}" aria-hidden="true" focusable="false">` +
    `<path fill="${ramp[0]}" d="M0 0h${BAND}v${H}H0z"/>` +
    kept.join('') + `</svg>`;
  const file = `${OUT_DIR}/plate-${side}.svg`;
  writeFileSync(file, body);
  const gz = gzipSync(Buffer.from(body)).length;
  console.log(
    `${side}: kept ${kept.length} paths (outside ${dropped}, culled ${culled}) ` +
    `raw ${(body.length / 1024).toFixed(0)}KB  gzip ${(gz / 1024).toFixed(0)}KB`
  );
}

build('left');
build('right');
console.log('ramp:', ramp.join(' '));
