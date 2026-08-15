# Margin plates — how public/ornament/* was derived

The project ships one piece of art: `hero-plate.svg` at the repo root, a traced
18th-century toile engraving (2912×1632, 64,513 paths, 10 MB, 29,285 distinct
fills). It cannot go into the render path as-is, and it cannot go in whole. Both
problems, and what was done about them:

## 1. The centre had to go

design-system.md §1 is unambiguous: *ornament lives in the margins, the centre is
always clean ivory, never behind text.* The source engraving has a pavilion,
figures and trees straight through its middle — measured, 21,096 paths and 3.0 MB
of ink sit in the centre 48% of the frame.

So the plate is **cut into its left and right thirds** (700px of source at each
edge) and the centre is discarded. What survives is exactly what the design
system asks for: scholars reading, statues, balustrade, cypress — turned inward,
framing an empty centre.

## 2. It had to become two inks, and small

- **Quantized** onto a 10-step ramp from `--paper` (#F7F3EA) to `--ink-cobalt`
  (#1B3A6B), so the plate is period-correct duotone and recolours with the
  tokens instead of fighting them.
- **Sky snapped to paper.** Anything at luminance ≥ 0.86 becomes `--paper`
  exactly, so the band dissolves into the page instead of sitting on it as a
  grey rectangle. The remaining range is rescaled across the ramp.
- **Rasterized, not shipped as SVG.** This was the interesting finding: even
  after aggressive path culling the two bands were ~800 KB *gzipped* and 7–20k
  DOM nodes each. The weight is the geometry, not the colours, so quantizing
  cannot fix it. At 900px wide the same artwork is **~170 KB as AVIF** with no
  visible loss at the size it is displayed. Traced SVG is the wrong format for
  photographic engraving detail; it is the right format for the fleurons and
  seals, which are drawn by hand in `components/Ornament.tsx` and
  `components/Seal.tsx`.

### One trap worth recording

The trace relies on painter order: a dark base rectangle is overpainted by a
large ivory ground shape. An early pass culled paths by bounding-box area at
*both* ends to save weight — dropping those two large shapes left flat dark
slabs across the foliage. **Only cull small paths, never large ones.**

## Regenerating

```bash
node scripts/build-plates.mjs ../../hero-plate.svg /tmp/plates 20 10
magick -background none /tmp/plates/plate-left.svg  -resize 900x -depth 8 -quality 58 public/ornament/plate-left.avif
magick -background none /tmp/plates/plate-left.svg  -resize 900x -depth 8 -quality 74 -define webp:method=6 public/ornament/plate-left.webp
# …and the same two commands for plate-right.
```

Args are `<src> <outDir> <minPathArea> <rampSteps>`.
