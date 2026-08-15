# The plate — how public/ornament/hero-*.{avif,webp} was derived

The project ships one piece of art: `hero-plate.svg` at the repo root, a traced
18th-century toile engraving (2912×1632, 64,513 paths, 10 MB, 29,285 distinct
fills). It is used **whole**. Only the encoding changes.

## Why it is a raster and not the SVG

The traced SVG cannot go in the render path: 64k paths is a photograph in vector
clothing. Measured, the file is ~3 MB gzipped and puts 64k nodes in the DOM. The
same artwork at display size is **160 KB (1800px) / 266 KB (2400px) as AVIF**,
visually identical at the sizes we show it.

Hand-drawn SVG is still the right format for the fleurons, seals and rules —
those live in `components/Ornament.tsx` and `components/Seal.tsx` and recolour
with the ink tokens. This note is about photographic engraving detail only.

## Encoding

```bash
for w in 1800 2400; do
  magick -background none ../../hero-plate.svg -resize ${w}x -depth 8 -quality 55 public/ornament/hero-$w.avif
  magick -background none ../../hero-plate.svg -resize ${w}x -depth 8 -quality 72 -define webp:method=6 public/ornament/hero-$w.webp
done
```

`globals.css` serves 1800 by default and 2400 above 1500px or 1.5dppx, with the
WebP declared first as the floor for browsers without typed `image-set`.

## The sky — the number the layout depends on

Luminance was sampled over the plate on a 12×7 grid:

```
 36  69  85  94  94  94  94  94  80  43  37  56
 38  54  70  92  94  94  94  94  71  45  44  45
 46  50  49  69  89  94  94  94  92  70  49  48
 40  45  39  53  76  93  92  89  75  61  51  48
 42  47  42  45  58  73  72  62  52  52  48  48
 57  55  43  51  47  55  64  46  45  57  50  54
 29  35  43  50  31  37  57  50  49  52  43  32
```

The region at roughly **x 29–71%, y 0–46%** holds 0.89–0.94 relative luminance —
indistinguishable from `--paper`. That is the sky, and per design-system.md §4 it
is the only part of the plate that may sit behind text. It is exported as `SKY`
from `components/Ornament.tsx` so the threshold screen positions against the same
numbers rather than a hand-tuned guess.

Measured on the rendered page at 1920×929 with `background-position: center 78%`,
the sky ends at **302px (33vh)**, and the hero content is sized to finish above
it. Verified in-browser by compositing the plate to a canvas and computing
worst-pixel contrast under each text element: **14.72 / 6.92 / 5.73** for the
wordmark, tagline and helper line — worst-pixel equals average, i.e. every one of
them sits on completely flat sky.

## Two traps worth recording

**Painter order.** An earlier pass culled paths by bounding-box area at both ends
to save weight, which dropped the trace's dark base rectangle *and* the large
ivory ground shape that overpaints it — leaving flat dark slabs across the
foliage. If you ever cull, **cull small paths only, never large ones.**

**Do not crop the plate to margins.** The first version of this app cut the plate
into two faded edge bands and threw the centre away. The centre is the whole
idea: a luminous empty sky with the scholars turned inward toward it. Cropping it
produced an app that hid ~70% of its own artwork. See the revision note at the
top of `design/design-system.md`.
