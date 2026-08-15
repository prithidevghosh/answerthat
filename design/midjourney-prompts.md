# Midjourney Prompts — Visual Direction

## What went wrong in v1, so we don't repeat it

Three specific mistakes, all mine:

1. **"Cabinet of curiosities" literally means "crowded with objects."** The phrase instructed
   Midjourney to fill the frame. The reference image works because it is a *garden* — open ground,
   objects pushed to the margins.
2. **Exotic abstract nouns render as mush.** `astrolabe`, `armillary sphere`, `celestial chart
   whose stars are joined by ruled lines` — Midjourney has weak, inconsistent priors for these, so
   it produced meaningless shapes. The reference sticks to things it has seen ten thousand times:
   trees, temples, statues, balustrades. **Ask only for objects with strong priors.**
3. **I dropped `toile de Jouy`.** That single term is what produces coherent pastoral scenes in two
   inks. It was the load-bearing phrase in your reference and I replaced it with vaguer language.
4. `--stylize 250` gave the model artistic licence exactly where we needed obedience.

**The rule going forward: describe a scene, not an inventory.** And name the empty centre three
different ways, because Midjourney weights repetition.

---

## Primary prompt — the upload screen

```
18th century French copperplate engraving, toile de Jouy, deep cobalt blue ink on ivory white
paper, thousands of engraved lines, cross-hatching, stippling, a gathering of curious 18th
century French scholars in frock coats and tricorn hats, holding books scrolls and folios, some
seated on stone benches, some standing in small conversing groups, all turned inward and gazing
toward the open center of the scene, neoclassical garden pavilion with columns, marble statues on
plinths, stone balustrades and urns, tall poplars cypress and chestnut trees, botanical
ornamentation, all figures and architecture arranged along the far left and far right margins and
the lower corners, vast empty ivory white ground through the entire center, wide open negative
space in the middle third, museum engraving, ultra intricate, collectible print aesthetic, no
typography, no logo, no watermark
--ar 16:9 --style raw --s 150 --no text, letters, signature, objects in center, crowded center
```

The scholars gazing inward is the idea worth keeping: the empty centre is where the researcher's
paper goes, and the whole 19th-century academy is turned toward it, waiting to read it. That earns
the ornament instead of decorating over it.

---

## Composition variants

**Wider centre** (if the primary still crowds):

```
…same subject text… , figures and trees confined to a narrow vertical band at the extreme left
edge and the extreme right edge only, two thirds of the image is empty ivory paper, sparse,
airy, generous margins
--ar 21:9 --style raw --s 125 --no text, letters, objects in center
```

**Side panel / vertical** (for the parse inspector's outer margin):

```
18th century French copperplate engraving, toile de Jouy, deep cobalt blue ink on ivory white
paper, cross-hatching and stippling, three curious French scholars in frock coats reading a
folio beneath a tall cypress, a marble statue on a plinth, stone balustrade, botanical
ornamentation along the lower edge, tall empty ivory space above, sparse composition, museum
engraving, no typography, no logo, no watermark
--ar 9:16 --style raw --s 150 --no text, letters, clutter
```

**Empty-state vignette** ("no findings yet"):

```
18th century French copperplate engraving, toile de Jouy, deep cobalt blue ink on ivory white
paper, a single curious French scholar in a frock coat standing beside an empty stone lectern
in a garden, one cypress behind, vast empty ivory paper surrounding, extreme negative space,
delicate hairline engraving, sparse, quiet, museum engraving, no typography, no logo, no
watermark
--ar 4:3 --style raw --s 125 --no text, letters, crowd, clutter
```

**Seamless paper texture:**

```
seamless tileable ivory white laid paper texture, faint horizontal chain lines, subtle
letterpress plate impression, no imagery, no ink, extremely subtle
--tile --ar 1:1 --style raw --s 50
```

---

## Midjourney technique — how to actually get the empty centre

Prompt wording alone won't reliably do it. Use the tools:

1. Generate at `--ar 16:9`.
2. **Zoom Out 1.5×** on the best result. This pushes all existing imagery toward the margins and
   fills the new centre with more of the same ground — it is the single most reliable way to open a
   centre, far better than asking for one.
3. If any object still intrudes, **Vary (Region)** — select the centre rectangle and prompt it with
   `empty ivory white paper, blank, no imagery`.
4. Keep `--s` between **100 and 150**. Low stylize = prompt obedience. That is what we want here;
   we are not asking for creativity, we are asking for a specific composition.
5. `--style raw` always. It suppresses Midjourney's default beautification, which is what adds
   invented clutter.

If a generation contains an object you can't identify, **discard it** — a shape that reads as
"something 18th century, unclear what" is exactly the failure you flagged, and it will look worse at
full screen than it does in a thumbnail.

---

## Asset list

| Asset | Prompt | Used for |
|---|---|---|
| Hero plate | Primary, above | Upload screen — left and right margins only |
| Side panel | Vertical variant | Parse inspector outer margin, at low opacity |
| Empty-state vignette | Vignette variant | "No findings yet", "no missing work found" |
| Paper texture | Texture prompt | Global background, **4% opacity maximum** |
| Ornament set | `set of 12 isolated engraved ornaments, fleurons, corner pieces, rules and dividers, toile de Jouy style, cobalt ink on pure white, evenly spaced grid, no typography --style raw --s 100` | Section dividers, card corners |
| Status seals ×3 | `single engraved wax seal emblem, {cobalt / sepia / sanguine} ink on ivory, isolated, simple, no typography --style raw --s 100` | Confidence-tier badges |

**Trace everything to SVG** (`vtracer` or Illustrator image-trace) before it enters the app. Raster
engravings at this detail level will destroy your Lighthouse score, and traced SVG recolours cleanly
to the three ink tokens.

---

## The rule the frontend must inherit

**Ornament lives in the margins. The centre is always clean ivory.** That is not just how the hero
image is composed — it is how every screen is composed. Content sits in a calm centre column with
engraving only at the outer edges, and never, ever behind text.

The test is the one you gave me: a researcher reading forty findings at 11pm must feel their eyes
relax, not hurt. If a screen fails that test, the ornament comes out — the aesthetic is never worth
more than legibility.
