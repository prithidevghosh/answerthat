# design-system.md — Tokens and Rules

Derived from Midjourney Direction A ("The Cabinet of Citations"). This file, not the image, is what
gets implemented. Frontend agent: these tokens are the contract.

---

## 1. The governing idea

**Ornament lives in the margins. The centre is always clean ivory.**

This is the first rule and it outranks the rest. The engraving frames the content at the far left
and far right edges and the lower corners; the centre column is calm, uncluttered paper. Never place
ornament behind text. Never fill a centre because it looks empty — the emptiness *is* the design.

**Ceremony at the threshold, calm at the desk.** The upload screen carries the ornament at full
strength, still only at its margins. Every working surface after it is quieter still: hairline
rules, one accent, and almost no imagery.

**The acceptance test for any screen:** a researcher reading forty findings at 11pm must feel their
eyes relax, not hurt. If a screen fails that, the ornament comes out. The aesthetic is never worth
more than legibility, and a beautiful screen nobody can read for an hour is a failed screen.

**Status colours are ink colours.** Multi-plate engravings were printed in several inks, so our
error states are period-correct rather than bolted on. This matters: the honesty surfaces
(quarantined references, contradicted claims, orphaned anchors) are the *product*, and they must be
the most beautiful part of the interface, not the ugliest.

---

## 2. Colour

```css
/* Ground */
--paper:          #F7F3EA;   /* ivory laid paper */
--paper-deep:     #EFE8DA;   /* recessed panels, table stripes */
--plate:          #FFFFFF;   /* raised cards, "fresh plate" */

/* Inks — semantic, not decorative */
--ink-cobalt:     #1B3A6B;   /* primary. resolved, verified, body emphasis */
--ink-cobalt-lt:  #4A6EA0;   /* secondary text, inactive rules */
--ink-sepia:      #8A6A3D;   /* UNCERTAIN: low confidence, partially_supports, unverifiable */
--ink-sanguine:   #9B3B2F;   /* FAILED: quarantined, contradicts, orphaned, rejected */
--ink-verdigris:  #2F6B5F;   /* CONFIRMED ACTION: approved change, successful export */

/* Text */
--text-primary:   #16202E;
--text-secondary: #55606E;
--text-muted:     #8A8F98;

/* Lines — engraving hairlines, never heavy borders */
--rule-hair:      rgba(27,58,107,0.18);
--rule-strong:    rgba(27,58,107,0.38);
```

**Contrast is non-negotiable.** Every ink above meets WCAG AA on `--paper` at 14px+. If a design
choice would drop a status colour below AA, the design choice loses. Status is **never conveyed by
colour alone** — every tier carries an icon and a text label.

### Status mapping — memorise this

| State | Ink | Label shown | Icon |
|---|---|---|---|
| `resolved` | cobalt | "Resolved" + link to source | filled seal |
| `parsed_unresolved` | sepia | "Parsed, not found in any index" | open seal |
| `low_confidence` | sepia | "Low confidence — fields uncertain" | half seal |
| `quarantined` | sanguine | "Could not parse" + **raw string verbatim** | broken seal |
| `orphan_marker` | sanguine | "Marker with no reference" + location | dangling rule |
| `supports` | cobalt | quote shown | quotation mark |
| `partially_supports` | sepia | quote shown | half quotation mark |
| `contradicts` | sanguine | quote shown | inverted quotation mark |
| `unverifiable_no_abstract` | sepia | "No abstract available — cannot verify" | empty frame |

---

## 3. Type

```css
--font-display: "Cormorant Garamond", "EB Garamond", Georgia, serif;  /* headings, plate numbers */
--font-body:    "Source Serif 4", Charter, Georgia, serif;            /* prose, findings, paper text */
--font-ui:      "Inter", system-ui, sans-serif;                       /* controls, labels, badges */
--font-mono:    "JetBrains Mono", ui-monospace, monospace;            /* raw strings, LaTeX, DOIs */
```

Body text is **serif**, because the user is reading a research paper and this is not a dashboard.
Interface chrome is sans, so controls never masquerade as manuscript.

Scale (1.25 minor third): `12 / 14 / 16 / 20 / 25 / 31 / 39 / 49`. Body 16px, line-height 1.65,
measure capped at **68ch** — a finding is prose, treat it as prose.

Raw quarantined strings and DOIs render in `--font-mono` at 14px. They are evidence; they must look
like evidence.

---

## 4. Form

- **Hairlines, not borders.** `1px solid var(--rule-hair)`. No shadows heavier than
  `0 1px 2px rgba(22,32,46,0.06)`. Engravings have no drop shadows.
- **Corner fleurons instead of rounded corners.** Cards are square (`border-radius: 2px`) with a
  traced SVG fleuron absolutely positioned at two opposing corners at 40% opacity.
- **Paper texture** applied globally at **4% opacity maximum**. Above that it fights the text. Test
  it against a full findings list, not against an empty screen.
- **Ornament placement is a hard constraint, not a preference.** Engraved imagery may appear only:
  in the outer margins (outside the content column), in the lower corners, and inside dedicated
  empty-state panels. It may never sit behind body text, behind a finding, or behind a diff. On
  viewports under 1280px the margin ornament is **removed entirely**, not scaled down.
- **Content column caps at 1100px** and stays centred, with the ornament outside it. That column is
  the calm centre the whole aesthetic is built around.
- **Rules as structure.** Section dividers are a hairline with a centred fleuron, not a grey bar.
- Spacing scale: `4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96`. Screens breathe — the aesthetic dies
  under dense padding.

---

## 5. Screen-by-screen

**Upload (the threshold).** Engraved plate at the left and right margins — scholars turned inward,
gazing at the centre — and a **large empty ivory field** holding one drop target and one line of
text. No nav, no marketing, no feature list. The product *is* the upload. This is the one screen
where the ornament runs at full strength, and even here it never enters the centre.

The idea worth protecting: the empty centre is where the researcher's paper goes, and the whole
academy is turned toward it, waiting to read it.

**Parse inspector (the plate).** Two columns inside the content column: document structure left,
references right. Margin ornament at very low opacity, or absent. Each
reference is a card carrying its tier seal, its rendered citation, and — when quarantined — its raw
string in mono, verbatim, never truncated. A count strip across the top: `38 resolved · 4 parsed,
not found · 2 could not parse · 1 orphan marker`. **Those counts are the honesty guarantee made
visible; give them prominence, not a footnote.**

**Review feed (the readings).** Findings stream in as they verify, highest citability first.
Each finding: the claim in serif, the verification label with its ink and icon, the verbatim quote
in an indented rule-bordered block, and the source with a real external link. A live
`verified 23 / 47` counter that never lets in-progress read as complete.

**Edit console (the desk).** Command input at the bottom, proposed changes above as diff cards —
removed text sanguine strikethrough, added text verdigris, **citation anchors rendered as small
seals that visibly persist across the diff**. Per-change approve/reject. Orphaned anchors surface as
their own card with three explicit buttons: keep here / move to… / remove.

**Export.** A plate-impression animation, then the `.tex` download. State the scope cut plainly:
figures, tables, and equations are placeholders.

---

## 6. Motion

Ink and paper move slowly. Transitions 180–240ms, `cubic-bezier(0.2, 0, 0.2, 1)`. Findings fade and
rise 8px as they stream in — never slide or bounce. The one indulgence: a **plate-impression press**
on export (a 400ms scale from 1.02 → 1.00 with a brief texture darkening). Once, at the end, as
punctuation.

`prefers-reduced-motion` removes all of it. No exceptions.

---

## 7. Hard rules

1. Accessibility beats aesthetics, every time. AA minimum, keyboard reachable, visible focus rings
   (2px `--ink-cobalt`, 2px offset).
2. **Ornament never sits behind text**, at any opacity, on any screen. If a layout seems to need it,
   the layout is wrong.
3. **Never add imagery to fill space.** Empty ivory is the intended state, not an unfinished one.
2. No status conveyed by colour alone — icon and text label always.
3. Ornament SVGs are `aria-hidden="true"` and never carry meaning.
4. Traced SVG ornaments only. No raster engravings in the render path.
5. Failure states get the same design care as success states. If the quarantine card looks like an
   afterthought next to the resolved card, the design has failed the product.
