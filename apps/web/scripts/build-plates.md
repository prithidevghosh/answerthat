# The plates — how public/ornament/* was derived

The project ships five pieces of art, at the repo root:

| Source | Size | Becomes | Used on |
|---|---|---|---|
| `hero_banner.png` | 2912×1632 | `frontispiece-{1700,2600}` | the threshold |
| `side_engraving_1.png` | 1792×2688 | `margin-1-{900,1400}` | Pl. I — parse |
| `side_engraving_2.png` | 1792×2688 | `margin-2-{900,1400}` | Pl. II — review |
| `side_engraving_3.png` | 1792×2688 | `margin-3-{900,1400}` | Pl. III — edit |
| `side_engraving_4.png` | 1792×2688 | `margin-4-{900,1400}` | Pl. IV — export |

Each is used **whole**. Only the encoding changes — nothing is cropped, because
the empty field in each composition is the part the layout is built on.

## Why they are rasters

These are photographic engraving detail: thousands of hairlines, cross-hatching
and stippling. The sources are 9–11 MB PNGs, which cannot go in the render path.
At display size they encode to 129–530 KB as AVIF, visually identical.

Hand-drawn SVG is still the right format for the fleurons, seals and rules —
those live in `components/Ornament.tsx` and `components/Seal.tsx` and recolour
with the ink tokens. This note is about photographic engraving detail only.

## Encoding

Run from `apps/web`:

```bash
OUT=public/ornament
for w in 1700 2600; do
  magick ../../hero_banner.png -resize ${w}x -strip -depth 8 -quality 58 $OUT/frontispiece-$w.avif
  magick ../../hero_banner.png -resize ${w}x -strip -depth 8 -quality 74 -define webp:method=6 $OUT/frontispiece-$w.webp
done

for i in 1 2 3 4; do
  for w in 900 1400; do
    magick ../../side_engraving_$i.png -resize ${w}x -strip -depth 8 -quality 56 $OUT/margin-$i-$w.avif
    magick ../../side_engraving_$i.png -resize ${w}x -strip -depth 8 -quality 72 -define webp:method=6 $OUT/margin-$i-$w.webp
  done
done
```

`globals.css` serves the smaller encode by default and the larger above 1500px
or 1.5dppx, with the WebP declared first as the floor for browsers without typed
`image-set`.

## The measurements the layout depends on

Both numbers below are sampled from the art, not chosen. If the art is ever
replaced, re-measure before adjusting the CSS.

### The frontispiece — the open field

Relative luminance on a 12×7 grid puts **x 25–72%, y 0–48%** at the same value
as `--paper`. That rectangle is the only place text may sit on the plate
(design-system.md §4). `OPEN_FIELD` in `Ornament.tsx` carries the same numbers.

### The margin plates — where the ink is

Ink coverage (pixels at saturation > 0.18) per 10% band. Every plate is
left-heavy and bottom-heavy, and consistently so:

```
columns, left → right          rows, top → bottom
plate 1:  54 56 58 70 51 50 39 25 15 17     8 34 30 25 30 35 48 69 79 78
plate 2:  57 61 71 65 47 36 31 24 17 27     4 20 25 27 24 33 57 77 81 87
plate 3:  47 52 65 67 43 30 25 27 24 41     3 26 28 32 31 33 42 75 78 76
plate 4:  32 53 60 66 49 42 23 24 16 16     0 13 27 19 26 32 46 61 76 81
```

So `.margin-plate` anchors `left bottom` and masks out to the right and up
toward the top — it dissolves into the paper exactly where its own ink is
already thinning, rather than being scrimmed back with opacity.

## Palette

All three plates sampled independently return hue **217–220°** at saturation
**0.60–0.62**, on a warm ivory of `#F2ECE7`–`#F5F1EA`. The ink ladder in
`globals.css` is built on that hue; see design-system.md §2 for the measured
contrast table.
