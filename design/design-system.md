# design-system.md — Tokens and Rules

Derived from the five engravings at the repo root: `hero_banner.png` (the frontispiece) and
`side_engraving_1..4.png` (the four margin plates). **The plates are the source of truth for this
system** — every colour below is sampled from them, not invented alongside them. Frontend agent:
these tokens are the contract.

> **Revision, 2026-08-16 (second).** The art was replaced: one wide plate for the threshold and four
> vertical plates for the working screens, in place of the single `hero-plate.svg`. This is not a
> reskin of the previous file. The vertical plates are drawn dense at the left and blank through the
> right two thirds, which is a layout instruction, not a texture — so the working screens changed
> from "calm ivory with a horizon band at the foot" to a **leaf with a margin vignette**, §1. The
> type changed with it, §3. The measured-contrast discipline of the previous file is kept exactly.

---

## 1. The governing idea

**Each plate has a deliberate empty field. The interface goes in that field.**

Look at the art before reading another line. Both compositions are doing the layout work for us:

- **The frontispiece** is dense at both edges and along the bottom — cypress and chestnut, statues on
  plinths, balustrades, scholars with folios, *all turned inward* — with a wide luminous ground
  across the upper centre at essentially the same value as the paper.
- **The four margin plates** are dense in the left half and bottom, and blank through the upper
  right. Measured: 50–70% ink coverage in the left five bands against 15–27% in the right three.

So there are exactly two placements, and they follow the art rather than fighting it:

**The threshold is a title page.** The frontispiece runs full-bleed at full strength, and the
wordmark, one line and one drop target sit in its open centre, arranged as an 18th-century title page
— standing head, title, rule, statement of contents. The empty centre is where the researcher's paper
goes, and the whole academy in the picture is turned toward it, waiting to read it.

**Every working screen is a leaf.** One margin plate is held in the outer margin and the text block
sits in the open field beside it — Pl. I parse, Pl. II review, Pl. III edit, Pl. IV export, so the
numeral on the stage and the plate on the page are the same fact stated twice.

**Do not crop the plates.** Do not fade them to a whisper, and never scrim them to make room for
text. Where a plate must dissolve into the paper, mask it along the axis its own ink is already
thinning on — see §4.

**Ceremony at the threshold, calm at the desk**, and that half is not negotiable either. The
threshold is allowed to be beautiful; the working screens must be *restful*, because a researcher
reads forty findings there at 11pm. **That is the acceptance test for every screen.**

**But restraint is not absence, and this is the line the first build of this system got wrong.** It
hid the margin plate below 1240px and ran it at 0.62 opacity with a mask that erased the top third —
which deleted the cypress and the poplar, the tallest things in the composition. The result was a
product whose threshold was a full-strength engraving and whose every working screen was a blank
ivory page. They did not look related.

So: **there is no viewport at which a working screen carries none of the art**, and where it appears
it appears at full strength. The plate steps down, it never vanishes:

| Viewport | Treatment |
|---|---|
| ≥ 1240px | margin column, `clamp(300px, 25vw, 440px)` |
| 1024–1240px | margin column, 236px |
| < 1024px | foot band at the end of the document |

Calm is achieved by keeping the engraving **out of the text column** — by masking along its own
grain and by reserving space for it — not by fading it until it is gone. If a working screen ever
fails the 11pm test, move the plate or narrow it; do not bleach it.

---

## 2. Colour

Sampling the frontispiece and the margin plates independently returns hue **217–220°** at saturation
**0.60–0.62** every time, on a warm ivory of `#F2ECE7`–`#F5F1EA`. The plates are a **single-ink
suite** — a cobalt ladder on warm ivory — so the interface is too. Three accent inks are added for
status; they are pitched to the same value depth and low saturation, so they read as further plates
from the same press rather than as UI chrome dropped on top.

```css
/* Grounds — the plates' own paper */
--paper:          #F3EDE6;   /* the leaf */
--paper-deep:     #E8E0D5;   /* recessed panels, raw-string blocks, table stripes */
--leaf:           #FBF8F3;   /* a fresh sheet — raised cards */

/* The cobalt ladder — hue 219, light to dark */
--ink-palest:     #C5CBD9;   /* farthest wash */
--ink-pale:       #9AA6BF;
--ink-mist:       #6E7FA4;   /* UI edges and large text only */
--ink-slate:      #4C6089;   /* secondary text, inactive rules */
--ink-cobalt:     #2E4B7E;   /* PRIMARY. the signature ink of the plates */
--ink-deep:       #16273F;   /* the darkest bite */

/* Status inks — same press, different plates */
--ink-sepia:      #6B5127;   /* UNCERTAIN: low confidence, partially_supports, unverifiable */
--ink-madder:     #8C3324;   /* FAILED: quarantined, contradicts, orphaned, rejected */
--ink-verdigris:  #245C4E;   /* CONFIRMED ACTION: approved change, successful export */

/* Text */
--text-primary:   #111E31;
--text-secondary: #3E4E68;
--text-muted:     #55627A;

/* Lines — engraved hairlines, never heavy borders */
--rule-hair:      rgba(46,75,126,0.15);
--rule-fine:      rgba(46,75,126,0.28);
--rule-strong:    #717F9A;
```

**Contrast is non-negotiable, and it has been measured, not eyeballed.** Against `--paper` /
`--leaf` / `--paper-deep` respectively:

| Token | paper | leaf | paper-deep | cleared |
|---|---|---|---|---|
| `--ink-cobalt` | 7.45 | 8.18 | 6.62 | AA text |
| `--ink-slate` | 5.41 | 5.93 | 4.80 | AA text |
| `--ink-mist` | 3.45 | 3.79 | 3.07 | **UI / large text only** |
| `--ink-deep` | 12.94 | 14.20 | 11.50 | AA text |
| `--ink-sepia` | 6.38 | 7.00 | 5.67 | AA text |
| `--ink-madder` | 6.91 | 7.58 | 6.14 | AA text |
| `--ink-verdigris` | 6.65 | 7.30 | 5.91 | AA text |
| `--text-primary` | 14.41 | 15.81 | 12.80 | AA text |
| `--text-secondary` | 7.24 | 7.95 | 6.44 | AA text |
| `--text-muted` | 5.29 | 5.81 | 4.70 | AA text |
| `--rule-strong` | 3.47 | 3.81 | 3.08 | 3:1 UI |
| `--ink-pale`, `--ink-palest` | — | — | — | **decorative only** |

Every text ink clears AA (4.5:1) on all three grounds. `--ink-mist` and `--rule-strong` clear the 3:1
required of an interactive edge but **not** body text — do not set prose in them. If a design choice
would drop an ink below its line in this table, the design choice loses.

Status is **never conveyed by colour alone** — every tier carries a seal icon and a text label, and
the seals are drawn to differ in *shape*, so they survive greyscale.

### Status mapping — memorise this

| State | Ink | Label shown | Seal |
|---|---|---|---|
| `resolved` | cobalt | "Resolved" + link to source | filled seal |
| `parsed_unresolved` | sepia | "Parsed, not found in any index" | open seal |
| `low_confidence` | sepia | "Low confidence — fields uncertain" | half seal |
| `quarantined` | madder | "Could not parse" + **raw string verbatim** | broken seal |
| `orphan_marker` | madder | "Marker with no reference" + location | dangling rule |
| `supports` | cobalt | quote shown | quotation mark |
| `partially_supports` | sepia | quote shown | half quotation mark |
| `does_not_address` | sepia | quote shown | half quotation mark |
| `contradicts` | madder | quote shown | inverted quotation mark |
| `unverifiable_no_abstract` | sepia | "No abstract available — cannot verify" | empty frame |

---

## 3. Type

```css
--font-display: "Bodoni Moda", Didot, Georgia, serif;    /* headings, wordmark, counts */
--font-body:    "Spectral", Charter, Georgia, serif;     /* prose, findings, paper text */
--font-ui:      "Archivo", system-ui, sans-serif;        /* controls, labels, badges */
--font-mono:    "IBM Plex Mono", ui-monospace, monospace;/* raw strings, LaTeX, DOIs */
```

The pairing is the plates' own logic. **Bodoni is a Didone**: extreme stroke contrast, unbracketed
hairline serifs — the letterform being cut at exactly the moment these scenes depict. It carries the
whole period reference, so nothing else has to, and no other element needs to cosplay.

**Spectral does the reading.** It is a Production Type face drawn in Paris for long-form text on
screen: low contrast, generous x-height, real italics. A researcher reads forty findings on it at
11pm — a job Bodoni would do badly.

Interface chrome is sans, so controls never masquerade as manuscript.

Scale (1.26): `12 / 14 / 16 / 20 / 25 / 31 / 39 / 49 / 62`. Body 16px, line-height 1.68, measure
capped at **68ch** — a finding is prose, treat it as prose, and cap the *card* to its measure too
rather than letting it stretch and leave empty leaf beside every line.

**Display type never goes below 20px** (`lg`). Bodoni's thin strokes are genuinely hairlines and
break up below that on a low-DPR screen. Body copy, labels and any text under 20px are Spectral or
Archivo, never Bodoni.

**The engraved label** (`.engraved-label`) — 11px Archivo, 500, `0.14em` tracking, uppercase — is the
one typographic gesture that carries the plate-label feeling into the interface. Every badge, step
name, column head and section head is set this way. It is a shared class, not a hand-rolled string;
there is exactly one definition of it.

Raw quarantined strings and DOIs render in `--font-mono` at 14px. They are evidence; they must look
like evidence.

**Type on a plate.** The wordmark on the threshold may run large — 49px and up — in `--font-display`
at `--ink-deep`. It sits in the open field, where the plate is at paper value, so it needs no scrim,
no panel and no text-shadow. **If a piece of text seems to need a scrim to be readable on a plate, it
is in the wrong place — move it into the open field or off the plate.**

---

## 4. Form

- **Hairlines, not borders.** `1px solid var(--rule-hair)`. No shadows heavier than
  `0 1px 2px rgba(17,30,49,0.05)`. Engravings have no drop shadows.
- **Corners are square.** `border-radius: 0`. The rules do the ornament, not a radius.
- **Cards carry a status rule, not a tinted box.** A solid 2px ink down the outer edge, in the card's
  status colour. It is how a reader picks the two madder cards out of forty without reading a word.
  Corner fleurons are **opt-in** and reserved for a card that is the subject of a screen — at forty
  findings the old always-on treatment put 160 marks on one screen, which is noise pretending to be
  craft.
- **Rules as structure.** Section dividers are a hairline with a centred fleuron, or the double rule
  (one `--rule-fine`, one `--rule-hair`, 3px apart), never a grey bar.
- **No frosted glass.** A sticky header or composer that scrolling text passes under is **opaque**,
  with a hairline to sit on. Translucency plus `backdrop-blur` smears the lines running beneath it
  and reads as a rendering fault rather than as depth. Ink is opaque; so are the surfaces.
- **Paper texture** applied globally at **3.5% opacity maximum**. Test it against a full findings
  list, not against an empty screen.
- **Content column caps at 1140px.** A single-column reading surface caps its cards at 860px inside
  that, flush left with the heading above them.
- Spacing scale: `4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96`. Screens breathe.

### Placing the plates (normative)

1. **Content may sit on the frontispiece only inside the open field** — the measured region at
   **x 25–72%, y 0–48%**, which holds the same relative luminance as `--paper`. Never over the
   foliage, the balustrades, the figures or the foreground.

   **The floor is a cliff, not a fade.** Measured down the centre column (x 36–64%, the width a
   centred block actually occupies), the 5th-percentile luminance holds `--text-secondary` at
   **7.46:1 all the way to 50%**, then collapses: 3.10:1 at 50–55%, 2.29:1 at 60–65%, 1.37:1 by 65%.
   So 50% is a hard boundary with nothing gradual about it — a line that slips four points past it
   goes from comfortably AA to invisible. Everything on the threshold must end above it.

   This is also why the threshold's vertical rhythm is in `vh` and not pixels. With `cover` on a
   viewport narrower in aspect than the plate, the open field is a fixed *fraction* of viewport
   height, so a block built from fixed pixels outgrows it on short screens — which is exactly how
   the caption line ended up on the treeline at 1440×900, measuring 1.07:1 against the darkest
   pixels behind it. Re-measure after changing any size there.
2. **Never add a scrim, blur or overlay to make a plate carry text**, at any opacity. Dimming the
   engraving to make room for a label ruins the engraving and produces muddy text. Move the text.
3. **Mask along the grain, do not fade the whole plate.** `.margin-plate` anchors `left bottom` and
   masks out to the right and up toward the top, because that is where its own ink is already
   thinning (coverage table in `apps/web/scripts/build-plates.md`). A flat opacity knock-back is the
   wrong tool and looks like it.
4. **The plates are `aria-hidden` and `pointer-events-none`.** They carry no meaning and intercept no
   clicks.
5. **A fixed plate must never sit under text.** `.margin-plate` is `position: fixed`, which is only
   safe because `.leaf` reserves space for it. Both take their measurement from **`--margin-col`, a
   single CSS variable** — this was previously two numbers in two files with a comment asking future
   readers to keep them in step, which is precisely the arrangement that eventually puts the
   engraving underneath the findings. One declaration, no drift.
6. **The plates step down, they never vanish.** Under 1024px the margin column becomes a foot band
   (`.margin-foot`), in normal flow after the content so no text can cross it. Under 900px the
   frontispiece becomes a foot-of-screen band rather than a hero, and the plate mark is not drawn —
   the open field is too small to hold the wordmark and the cartouche at once.

---

## 5. Screen-by-screen

**Upload (the threshold).** The frontispiece, full-bleed, at full strength, anchored so its horizon
sits low and its open centre fills the upper screen. Set as a title page: standing head in engraved
caps, the wordmark in Bodoni, a rule with a fleuron, one italic line, the cartouche, and the terms
as an engraved label. No nav, no marketing, no feature list — the product *is* the upload. The whole
block is **centred in the open field**, not pinned to the top of it; pinned, it read as crammed with
all the slack dumped into one gap below.

**The upload control is a cartouche, not a dropzone.** It is a compact framed plaque — ~364×74, an
open double rule, two centred lines of set type, no icon and no fill. It is deliberately *not* a
large hollow rectangle, for three reasons, in order of importance:

1. A big empty box is a foreign object on an engraving. A cartouche — the framed label plaque a
   plate actually contains — is native to the medium and reads as a designed object rather than a
   void.
2. It does not need to be large. The drag listeners are bound to `window`, so **the whole screen is
   already the drop area**; this element is only the affordance and the click target.
3. The ~70px it gives back is what keeps the whole block above the 50% cliff on a short screen.

Centred, not icon-left-of-text: a long line over a short one, left-aligned beside an icon, measured
centred and still read left-heavy, with a void in the bottom-right of the plaque. The caption
beneath stays at the standard engraved-label size so it is **narrower than the plaque it captions**;
a step larger and it runs wider, which reads as a heading rather than a footnote.

The error state is the one element allowed past the open field — an error must be shown wherever it
happens — so it brings its own opaque `--leaf` ground rather than relying on the field being light.
A card with its own ground is the allowed way to do that; bare text over the engraving is never.

**The fork.** The file is sent the moment it is dropped, and while those bytes are in flight the
threshold offers the two routes through the product: **Guided** (you drive — parse, review, edit,
export, one screen each) and **Conversational** (an assistant drives, and you tell it what to do).
The pick decides what happens when the `202` lands, not whether the upload happens.

Two cartouches side by side, in the language of the one they replace: open double rule, square
corners, set type, no icon, no fill. Not two filled buttons — a filled control is a foreign object
on an engraving, which is the whole reason the drop target is a cartouche — and not a toggle, since
these are two named places to go rather than one setting. Under the pair, one engraved-label line
naming the difference; under that, the upload as a hairline that fills with its stage in words.
Below 900px they stack, where the frontispiece is already a foot band.

**The budget was re-measured for it, in a browser.** This block is 140px, which makes it the
tallest state the threshold has — taller than the 105px cartouche-and-caption it replaces — so it
is now the state that governs the 50% cliff:

| viewport | cliff | block ends at | spare |
|---|---|---|---|
| 1024×640 | 320 | 314 | 6 |
| 1280×720 | 360 | 342 | 18 |
| 1366×768 | 384 | 360 | 24 |
| 1440×900 | 450 | 417 | 33 |
| 1920×1080 | 540 | 485 | 55 |

Six pixels at 1024×640 is the real margin, which is why the two gaps inside the block sit at the
tight end of the spacing scale instead of stepping up with viewport height as the title page above
them does. **Add a line here and re-measure.** The route caption goes first — the cartouches' own
second lines already name the difference — and crushing the spacing is the wrong trade.

**Parse inspector (Pl. I).** Two columns inside the content column: document structure left,
references right. A count strip across the top: `38 resolved · 4 parsed, not found · 2 could not
parse · 1 orphan marker`, with the numerals in Bodoni at headline size. **Those counts are the
honesty guarantee made visible; give them prominence, not a footnote.** Quarantined raw strings
render verbatim in mono, never truncated.

**Review feed (Pl. II).** The screen the 11pm test was written for. Findings stream in as they
verify, highest citability first, capped to the reading measure. Each: the claim in serif, the
verification label with its ink and seal, the verbatim quote in an indented rule-bordered block, and
the source with a real external link. A live `verified 23 / 47` counter that never lets in-progress
read as complete.

**Edit console (Pl. III).** Command input at the bottom, proposed changes above as diff cards —
removed text madder strikethrough, added text verdigris, **citation anchors rendered as small seals
that visibly persist across the diff**. Per-change approve/reject. Orphaned anchors surface as their
own card with three explicit buttons: keep here / move to… / remove.

**Export (Pl. IV).** The last plate of the suite, closing the arc the frontispiece opened. A
plate-impression animation, then the `.tex` download. State the scope cut plainly: figures, tables
and equations are placeholders.

**Conversation.** The second path: one screen where an assistant does the work and narrates it. It
is the easiest screen in the product to get visually wrong, because every chat UI reference in
existence is built from rounded bubbles, avatars, drop shadows and frosted glass, and all four are
forbidden here.

*A printed dialogue, not a messaging app.* Turns are told apart **typographically**. User turns are
`--font-ui` at 14px, indented behind a 2px `--ink-cobalt` rule down the left edge. Agent turns are
`--font-body` at reading size, full measure, no rule and no container — the agent's prose is the
body text of the page. No avatars. No timestamps on every message. No right alignment: this is a
record of a conversation about a manuscript and it reads top to bottom in one column. A hairline
with a centred fleuron separates a speaker change, and only a speaker change; a divider between
every message is a table of contents for a conversation. The transcript caps at 860px, the measure
`ReviewFeed` uses, flush left with the heading.

*The composer is opaque.* It is pinned to the bottom with a hairline to sit on, and it carries no
`backdrop-blur` and no translucency at any opacity — §4's rule, and this is the screen with the
most text running under the most sticky chrome. Above it, one engraved-label line derived from live
state ("The bibliography is still reconciling") and otherwise nothing. Empty ivory is the intended
state; suggestion chips are the screen telling the user what to want. Sending disables the textarea
and raises **Stop**, and focus is *handed over* to Stop rather than merely "returned to the
composer" — a disabled element cannot hold focus, and a keyboard user stranded on `<body>` with a
six-minute turn running is the trap the control exists to prevent.

*Tool calls are sealed lines.* Each is one line inside the agent's turn: a `<Seal>` for state, the
registry's own `label` in engraved caps, the tool's `summary`. In flight is `half` in cobalt,
succeeded is `filled` in verdigris, failed is `broken` in madder **with the reason shown in full**.
Seal *and* text label, never ink alone. Arguments collapse behind a disclosure. Forty of these
cannot each be a panel, which is the point of the single line.

*The card suite is reused, not rebuilt.* A finding in the conversation is the same `<FindingCard>`
the review feed shows; a proposed change is the same `<ChangeCard>` with the same `<DiffText>` and
`<AnchorSeals>`; the tier counts get `<CountStrip>` with Bodoni numerals at headline size in the
chat exactly as on Pl. I, because they are the honesty guarantee and demoting them to a sentence
would be the one place this flow quietly promised less than the guided one. Where a card took
screen-level props they were lifted (`ChangeCard` gained `readOnly`, `DocumentStructure` takes
`sections`); a second card that drifts from the first is a worse outcome than an awkward prop. Long
results collapse to three and a disclosure.

*Progress is a hairline that fills*, `h-px bg-cobalt`, with the phase stated in words beside it —
never a rounded track, never a spinner, never a bar animated to fill a silence. It is pinned above
the composer while running and settles into the transcript at the point it completed, because a
finished run is part of the record of what happened.

*No stepper.* `WorkbenchHeader` carries the document identity here and nothing else — the four
stage tabs are **not** shown. A stepper on this screen says the conversation is one phase of a
four-phase march, which is the opposite of what the flow is: the agent does all four, in whatever
order it is asked to, and none of them is a place the user has to go. One quiet crossing link —
"Guided screens →" — is the whole of it. The guided screens stay readable, same document and same
versions, and a user who wants to study a diff on Pl. III arrives there and picks the stepper up
from that side.

*The plate follows the work.* Pl. I while parsing, II while reviewing, III on a pending edit, IV on
a pending export. The four plates are the four stages and this screen does all four in turn, so
pinning it to one numeral would put the parse engraving beside a running review. That is also why
the plate carries the stage here and the header does not: an ornament that tracks the work reads as
atmosphere, while a tab row that tracks it reads as an instruction.

*Confirmation is a message, never a request.* Yes and No post canonical text into the conversation;
neither calls an endpoint. Orphaned anchors each get their own `<OrphanedAnchorCard>` with the three
explicit choices, and Yes stays disabled until every one has a decision — HR-5, stated on this
screen as well as in the runtime, deliberately twice.

*Motion* is the same 180–240ms `ease-ink`: messages fade and rise 8px, nothing slides or bounces.
No typing dots — a `<Seal kind="half">` beside the word "Thinking" says it in this typeface — and
no per-character animation on top of a stream that is already progressive.

---

## 6. Motion

Ink and paper move slowly. Transitions 180–240ms, `cubic-bezier(0.2, 0, 0.2, 1)`. Findings fade and
rise 8px as they stream in — never slide or bounce. The rule under the current stage is *drawn* left
to right rather than filled. The one indulgence: a **plate-impression press** on export (a 400ms
scale from 1.02 → 1.00 with a brief texture darkening). Once, at the end, as punctuation.

`prefers-reduced-motion` removes all of it. No exceptions.

---

## 7. Hard rules

1. **Accessibility beats aesthetics, every time.** AA minimum on every text ink, 3:1 on every
   interactive edge, keyboard reachable, visible focus rings (2px `--ink-cobalt`, 2px offset).
2. **Text goes on a plate only in its open field** (§4). Never over foliage or figures, never behind
   a scrim, at any opacity.
3. **Never crop a plate to a decorative sliver.** Each is a composition, not a texture. Show it whole
   and mask it along its own grain; do not reduce it to a faded edge.
4. **Never add imagery to fill space.** On the working screens, empty ivory is the intended state.
5. No status conveyed by colour alone — seal and text label, always.
6. Ornament SVGs are `aria-hidden="true"` and never carry meaning. That includes the plate numerals:
   "Pl. II" is decorative shorthand beside the real word "Review", never a replacement for it.
7. Traced SVG for fleurons, seals and rules; the plates themselves ship as pre-encoded rasters,
   because at this line density they are photographs (see `apps/web/scripts/build-plates.md`).
8. **Failure states get the same design care as success states.** If the quarantine card looks like
   an afterthought next to the resolved card, the design has failed the product.
