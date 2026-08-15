# design-system.md — Tokens and Rules

Derived from the engraved plate at `hero-plate.svg` ("The Cabinet of Citations"). **The plate is the
source of truth for this system** — every colour below is sampled from it, not invented alongside
it. Frontend agent: these tokens are the contract.

> **Revision, 2026-08-16.** The previous version of this file told the frontend to crop the plate
> into two faded margin bands and keep the centre empty. That was a misreading of the artwork. The
> plate *already contains* its empty centre — a luminous open sky, framed by scholars turned inward
> — and cropping it away destroyed the one idea the image was built around. This revision replaces
> the margin-band rule with the composition the plate actually has. See §1.

---

## 1. The governing idea

**The plate is the stage. The content sits in the sky the plate already gives us.**

Look at the engraving before reading another line. Its composition is doing the work for us:

- **Dense at both edges** — cypress and chestnut, statues on plinths, stone balustrades, and
  scholars seated with folios, *all turned inward*.
- **A colonnaded rotunda in the middle distance**, with figures conversing in the foreground centre.
- **A wide, luminous, all but empty sky across the upper centre**, at essentially the same value as
  the paper itself.

That sky is not a gap to be covered. It is the lectern. The whole academy in the picture is turned
toward it, waiting to read what gets placed there — so the researcher's paper goes *into the plate*,
not onto a blank page next to it.

**Do not crop the plate to its margins.** Do not fade it to a whisper. On the threshold screen it
runs full-bleed and at full strength, and the interface sits inside its composition.

**Ceremony at the threshold, calm at the desk.** That is the other half, and it is not negotiable
either. The upload screen is the plate. Every working surface after it — parse, review, edit, export
— is quiet ivory, because a researcher reads forty findings there at 11pm. The plate appears on
those screens only as a **horizon band**: a single strip along the very bottom of the viewport,
below all content, or not at all.

**The acceptance test for any screen:** a researcher reading forty findings at 11pm must feel their
eyes relax, not hurt. The threshold screen is allowed to be beautiful; the working screens must be
*restful*. If a working screen fails that test, the plate comes out of it entirely.

---

## 2. Colour

Every value below is sampled from `hero-plate.svg`. The plate is a **single-ink engraving** — a
ladder of indigo on warm ivory — so the interface is too. Three accent inks are added for status;
they are pitched to the same value depth and low saturation, so they read as further plates from the
same press rather than as UI chrome dropped on top.

```css
/* Ground — the plate's own paper */
--paper:          #EFECE6;   /* ivory laid paper, sampled from the plate */
--paper-deep:     #E4DFD6;   /* recessed panels, raw-string blocks, table stripes */
--plate:          #F7F5F1;   /* raised cards, "fresh plate" */

/* The indigo ladder — sampled from the engraving, light to dark */
--ink-palest:     #BCC3CE;   /* farthest wash, distant foliage */
--ink-pale:       #919FB6;
--ink-muted:      #7589AA;
--ink-slate:      #556D94;
--ink-steel:      #475A78;   /* secondary text, inactive rules */
--ink-indigo:     #1E3659;   /* PRIMARY. resolved, verified, body emphasis */
--ink-deepest:    #162436;   /* the plate's darkest bite */

/* Status inks — same press, different plates */
--ink-sepia:      #6E5330;   /* UNCERTAIN: low confidence, partially_supports, unverifiable */
--ink-madder:     #8A3428;   /* FAILED: quarantined, contradicts, orphaned, rejected */
--ink-verdigris:  #2A5F53;   /* CONFIRMED ACTION: approved change, successful export */

/* Text */
--text-primary:   #141F2E;
--text-secondary: #44536A;
--text-muted:     #525F75;

/* Lines — engraving hairlines, never heavy borders */
--rule-hair:      rgba(30,54,89,0.16);
--rule-strong:    #6F81A0;
```

**Contrast is non-negotiable, and it has been measured, not eyeballed.** Against `--paper` /
`--plate` / `--paper-deep` respectively:

| Token | paper | plate | paper-deep |
|---|---|---|---|
| `--ink-indigo` | 10.31 | 11.17 | 9.16 |
| `--ink-steel` | 5.93 | 6.43 | 5.27 |
| `--ink-sepia` | 6.05 | 6.55 | 5.38 |
| `--ink-madder` | 6.84 | 7.41 | 6.08 |
| `--ink-verdigris` | 6.23 | 6.74 | 5.53 |
| `--text-primary` | 14.08 | 15.24 | 12.51 |
| `--text-secondary` | 6.62 | 7.17 | 5.88 |
| `--text-muted` | 5.48 | 5.93 | 4.87 |
| `--rule-strong` (3:1 UI) | 3.35 | — | — |

Every text ink clears AA (4.5:1) on all three grounds. If a design choice would drop one below, the
design choice loses. Status is **never conveyed by colour alone** — every tier carries a seal icon
and a text label, and the seals are drawn to differ in *shape*, so they survive greyscale.

### Status mapping — memorise this

| State | Ink | Label shown | Seal |
|---|---|---|---|
| `resolved` | indigo | "Resolved" + link to source | filled seal |
| `parsed_unresolved` | sepia | "Parsed, not found in any index" | open seal |
| `low_confidence` | sepia | "Low confidence — fields uncertain" | half seal |
| `quarantined` | madder | "Could not parse" + **raw string verbatim** | broken seal |
| `orphan_marker` | madder | "Marker with no reference" + location | dangling rule |
| `supports` | indigo | quote shown | quotation mark |
| `partially_supports` | sepia | quote shown | half quotation mark |
| `does_not_address` | sepia | quote shown | half quotation mark |
| `contradicts` | madder | quote shown | inverted quotation mark |
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

**Type on the plate.** The wordmark on the threshold screen may run large — 49px and up — in
`--font-display` at `--ink-deepest`. It sits in the sky (§5), where the plate is at paper value, so
it needs no scrim, no panel, and no text-shadow. **If a piece of text seems to need a scrim to be
readable on the plate, it is in the wrong place — move it into the sky or off the plate.**

---

## 4. Form

- **Hairlines, not borders.** `1px solid var(--rule-hair)`. No shadows heavier than
  `0 1px 2px rgba(20,31,46,0.06)`. Engravings have no drop shadows.
- **Corner fleurons instead of rounded corners.** Cards are square (`border-radius: 2px`) with a
  traced SVG fleuron at two opposing corners at 40% opacity.
- **Paper texture** applied globally at **4% opacity maximum**. Test it against a full findings
  list, not against an empty screen.
- **Content column caps at 1100px** and stays centred on working screens.
- **Rules as structure.** Section dividers are a hairline with a centred fleuron, not a grey bar.
- Spacing scale: `4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96`. Screens breathe.

### Placing the plate (normative)

The plate's luminance was measured on a 12×7 grid. The region bounded by roughly **x 29–71%,
y 0–46%** holds a relative luminance of **0.89–0.94** — indistinguishable from `--paper`. That
rectangle is the sky.

1. **Content may sit on the plate only inside the sky.** Never over the foliage, the balustrades,
   the figures, or the foreground — those run to 0.29 luminance and no ink is readable on them.
2. **Never add a scrim, blur, or overlay to make the plate carry text.** Dimming the engraving to
   make room for a label ruins the engraving and produces muddy text. Move the text instead.
3. **The plate is `aria-hidden` and `pointer-events-none`.** It carries no meaning and intercepts
   no clicks.
4. **Below 900px the plate is not a hero.** The sky is too small to hold the wordmark and the drop
   target at once, so on narrow viewports the plate drops to a horizon band at the foot of the
   screen and the content runs on plain ivory above it.

---

## 5. Screen-by-screen

**Upload (the threshold).** The plate, full-bleed, at full strength, anchored so its horizon sits
low and its sky fills the upper screen. The wordmark, one line of text, and one drop target sit in
that sky, centred, with generous air. No nav, no marketing, no feature list. The product *is* the
upload.

The idea worth protecting: the empty sky is where the researcher's paper goes, and the whole academy
in the picture is turned toward it, waiting to read it.

**Parse inspector (the plate).** Calm ivory. Two columns inside the content column: document
structure left, references right. The engraving appears only as a horizon band along the bottom of
the viewport, or not at all. A count strip across the top: `38 resolved · 4 parsed, not found ·
2 could not parse · 1 orphan marker`. **Those counts are the honesty guarantee made visible; give
them prominence, not a footnote.** Quarantined raw strings render verbatim in mono, never truncated.

**Review feed (the readings).** Calm ivory, no plate behind the findings — this is the screen the
11pm test was written for. Findings stream in as they verify, highest citability first. Each: the
claim in serif, the verification label with its ink and seal, the verbatim quote in an indented
rule-bordered block, and the source with a real external link. A live `verified 23 / 47` counter
that never lets in-progress read as complete.

**Edit console (the desk).** Calm ivory. Command input at the bottom, proposed changes above as diff
cards — removed text madder strikethrough, added text verdigris, **citation anchors rendered as
small seals that visibly persist across the diff**. Per-change approve/reject. Orphaned anchors
surface as their own card with three explicit buttons: keep here / move to… / remove.

**Export.** The plate returns, as a horizon band or a full field, to close the arc that the upload
screen opened. A plate-impression animation, then the `.tex` download. State the scope cut plainly:
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

1. **Accessibility beats aesthetics, every time.** AA minimum on every ink, keyboard reachable,
   visible focus rings (2px `--ink-indigo`, 2px offset).
2. **Text goes on the plate only in the sky** (§4). Never over foliage or figures, never behind a
   scrim, at any opacity.
3. **Never crop the plate to a decorative sliver.** It is a composition, not a texture. Show it
   whole or show a deliberate band of it; do not reduce it to a faded edge.
4. **Never add imagery to fill space.** On the working screens, empty ivory is the intended state.
5. No status conveyed by colour alone — seal and text label, always.
6. Ornament SVGs are `aria-hidden="true"` and never carry meaning.
7. Traced SVG for fleurons, seals and rules; the plate itself ships as a pre-encoded raster, because
   at 64k paths it is a photograph in vector clothing (see `apps/web/scripts/build-plates.md`).
8. **Failure states get the same design care as success states.** If the quarantine card looks like
   an afterthought next to the resolved card, the design has failed the product.
