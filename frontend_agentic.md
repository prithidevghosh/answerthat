# frontend_agentic.md — build the agentic chat screen

You are adding a **second path** through answerthat. After a PDF is accepted, the user
chooses between the existing deterministic flow (parse → review → edit → export, one screen
each, user-driven) and a new **agentic flow**: a single conversational screen where an agent
does the work and narrates it.

The deterministic screens do not change. Every existing route, component and behaviour
stays. You are adding a fork and a new screen.

Read `design/design-system.md` end to end before you write a line of JSX. It is normative,
not aspirational, and this screen is the easiest one in the product to get visually wrong —
every chat UI reference you have ever seen is built from rounded bubbles, avatars, drop
shadows and frosted glass, and **all four are forbidden here**.

The backend contract is `backend_agentic.md`. Build against the SSE event names it defines.

---

## 0. The thing that would make this fake

- **No client-side orchestration.** The frontend never decides to start a review, propose an
  edit, or run an export. It renders what the agent does. If you find yourself writing
  `if (parseDone) startReview()`, stop — that logic belongs to the model, on the server.
- **Confirmation buttons send messages, they do not call endpoints.** When the agent asks
  "shall I commit this?", the Yes button posts a normal user message into the conversation.
  It must never call `/change-sets/{id}/approve` directly. The moment the UI can commit
  without the agent, the flow is deterministic again with extra steps.
- **No hardcoded agent copy.** Every sentence attributed to the agent came from the stream.
  The UI supplies labels for its own chrome ("Reading the parse report") and nothing else.
- **Progress bars render real events.** `progress` events carry real fractions from the
  backend. Never animate a fake bar to fill silence — the existing upload control already
  gets this right and says "This step does not report progress" when it cannot report one.

---

## 1. The fork after upload

Today `getClient().uploadPdf(file, onProgress)` posts the PDF **and then blocks polling
until the parse finishes**, and only then does `UploadDropTarget` navigate to `/parse`. Read
the long docstring above `uploadPdf` in
[live-client.ts](apps/web/src/lib/api/live-client.ts) before touching it — it exists because
navigating on the bare `202` used to 404 the parse inspector every time.

For the agentic path you need the opposite: navigate immediately, because the agent's whole
job is to narrate parsing as it happens. So split the two halves rather than weakening
either:

```ts
// lib/api/client.ts — ApiClient
uploadPdf(file, onProgress, signal): Promise<UploadAccepted>;   // resolves on the 202
waitForParse(docId, onProgress, signal): Promise<UploadAccepted>; // the existing poll loop
```

`UploadAccepted` gains `job_id`. The deterministic path is then
`uploadPdf(...).then(waitForParse)` — byte-identical behaviour to today — and the agentic
path is `uploadPdf(...)` followed by an immediate `router.push()` to the chat.

**Where the fork appears.** The file is sent as soon as it is dropped; the choice is offered
while the bytes are in flight, and the user's pick decides what happens when the `202`
lands. Two named routes, both stated as what they are:

- **Guided** — you drive. Parse inspector, review feed, edit console, export, one screen
  each. → `/documents/{id}/parse`
- **Conversational** — an assistant drives, and you tell it what to do. → `/documents/{id}/chat`

Design: this is on the threshold, so it must obey §4's placement rules. The whole block is
centred in the plate's **open field** and every element ends above the **50% cliff** — the
budget in `page.tsx` is measured, and adding two choices to it is exactly the kind of change
that pushes the last line onto the treeline. Re-measure. If it does not fit, cut something
else rather than crushing the spacing; the caption line is the first thing at risk.

Form: two cartouches side by side, matching the existing one's language — open double rule,
square corners, set type, no icon, no fill. Not two filled buttons, and not a toggle switch.
Under the pair, one engraved-label line naming the difference in a few words. Below 900px
they stack.

Persist the choice per document in `sessionStorage` so a refresh on the chat route does not
re-ask, and so the chat page can tell "you chose this" from "you typed the URL".

---

## 2. Client seam

Both clients implement `ApiClient` and are interchangeable — that is the whole point of the
seam. **If you add chat methods to `live-client.ts` and not to `fixture-client.ts`, the app
breaks the moment `NEXT_PUBLIC_USE_FIXTURES=1`, and it breaks at runtime rather than at
typecheck if you cheat with an optional method.** Add to both.

```ts
startConversation(docId: string): Promise<Conversation>;
getConversation(conversationId: string): Promise<ConversationLog>;   // cold load
sendMessage(conversationId: string, text: string): Promise<{ accepted: true }>;
subscribeChat(conv: Conversation, onEvent: (e: ChatEvent) => void): ChatHandle;
stopTurn(conversationId: string): Promise<void>;
```

`subscribeChat` follows the rules `subscribeReview` learned the hard way:

- **`EventSource` connects straight to FastAPI.** Never proxy SSE through a Next.js route
  handler — the handler buffers the stream and the agent's text arrives in one clump at the
  end, which is worse than no streaming at all.
- **Use the URL the server handed you** (`conversation.stream`), via `browserUrl()`. Do not
  compose paths. Composing them is how `/api/reviews/{job_id}/stream` — a route no router
  has ever served — shipped.
- A **named** `error` event from the server is terminal and carries a reason. A **bare**
  transport `Event` may reconnect. Treating the first as the second is what turned a named
  backend failure into "Reconnecting…" forever.
- The server says `done`; pick one vocabulary in `ChatEvent` and map at the boundary.

Type the events in `lib/api/types.ts` against the table in `backend_agentic.md` §7:
`message_start`, `message_delta`, `message`, `tool_call`, `tool_result`, `progress`,
`awaiting_confirmation`, `error`, `done`, `heartbeat`. Every payload gets a real interface;
no `any`, no `Record<string, unknown>` reaching a component.

---

## 3. `lib/useChatStream.ts`

Model it on [useReviewStream.ts](apps/web/src/lib/useReviewStream.ts). It owns:

- The message list, assembled from `message_start` + `message_delta` + `message`. Deltas
  append to the in-flight assistant message; the final `message` replaces it, so a dropped
  delta cannot leave corrupted text on screen.
- Tool calls and their results, attached to the assistant message that issued them, keyed by
  `call_id`. A `tool_call` with no `tool_result` yet renders as in-flight — a tool that never
  returns must look unfinished, not finished.
- Live parse and review progress from `progress` events, kept as their own state so the
  progress cards update without touching the transcript.
- The pending confirmation from `awaiting_confirmation`, cleared when the next user message
  is sent.
- `phase`: `idle | thinking | streaming | awaiting_confirmation | interrupted | failed`.
  Each is distinguishable on screen. "Waiting" and "broken" must never look alike.
- Cold load: `getConversation()` first, then subscribe. The server replays its event log, so
  reconcile by `message_id` rather than appending blindly, or a refresh doubles the
  transcript.

---

## 4. The screen: `app/documents/[docId]/chat/`

```
chat/page.tsx        server component — resolves doc, status probe, style, renders shell
chat/ChatConsole.tsx client component — the conversation
```

`page.tsx` follows the existing screens: `export const dynamic = 'force-dynamic'`, the HR-2
status probe with `<ConfigurationError>` when the API is misconfigured or unreachable, and
`<FixtureBanner />`. Do not skip these — the chat page is the first screen a user lands on
in this flow, so it is where a missing key will be discovered.

### Layout

- The margin-plate suite as on the other working screens, via `--margin-col`. Plates are
  `aria-hidden` and `pointer-events-none`, step down to a foot band under 1024px, and are
  never cropped to a sliver.
- Content column caps at 1140px; the transcript caps at **860px**, flush left with the
  heading — the same measure `ReviewFeed` uses. Do not let messages run the full width.
- `<WorkbenchHeader>` for the document identity, with a link across to the deterministic
  screens (they stay readable — the same document, the same versions).
- The composer is pinned to the bottom. §4: **it is opaque, with a hairline to sit on.** No
  `backdrop-blur`, no translucency, at any opacity — text passing under a frosted bar smears
  the rules beneath it and reads as a rendering fault.

### The transcript

**A printed dialogue, not a messaging app.** Turns are distinguished typographically, not by
coloured bubbles:

- **User turns**: `font-ui`, set slightly smaller, indented behind a 2px `--ink-cobalt` rule
  down the left edge. Right-aligned bubbles are wrong for this product — this is a record of
  a conversation about a manuscript, and it reads top to bottom in one column.
- **Agent turns**: `font-body` serif at reading size, full measure, no rule, no container.
  The agent's prose is the body text of the page.
- No avatars. No timestamps on every message — a hairline with the elapsed time between
  distant turns is enough, and only where there is a real gap.
- Turns are separated by space and, where a phase changes, by the double rule (one
  `--rule-fine`, one `--rule-hair`, 3px apart) or a hairline with a centred `<Fleuron>`.
- Square corners everywhere. `border-radius: 0`. Hairlines, never borders. No shadow heavier
  than `0 1px 2px rgba(17,30,49,0.05)`.

### Tool calls

A tool call is the agent doing something to the manuscript, and the user is entitled to see
it. Render each as a compact single line inside the agent's turn: a `<Seal>` for state, the
registry's `label` in `engraved-label` caps, and the `summary` from the result.

- in flight → `Seal kind="half"`, `--ink-cobalt`, label in present tense
- succeeded → `Seal kind="filled"`, `--ink-verdigris`
- failed → `Seal kind="broken"`, `--ink-madder`, **and the error reason shown in full**

Never colour alone (§7 rule 5): seal *and* text label, always. Arguments are collapsed
behind a disclosure for anyone who wants them; the default view is one line.

### Cards inside the transcript

When `tool_result.data` carries something structured, render the real component rather than
letting the agent describe it in prose. **Reuse, do not rebuild:**

| Tool result | Component |
|---|---|
| parse report | `<CountStrip>` — the tier counts are the honesty guarantee and they get Bodoni numerals at headline size, in the chat as much as on Pl. I |
| references, quarantine, orphan markers | `<ReferenceCard>`, `<OrphanMarkerCard>` |
| a finding | `<FindingCard>` with `<RenderedCitation>` |
| a proposed change | `<ChangeCard>` + `<DiffText>` + `<AnchorSeals>` |
| an orphaned anchor | `<OrphanedAnchorCard>` |
| a kernel rejection | `<RejectedOperationCard>` — reasons verbatim, never softened |
| export manifest | the placeholder disclosure, stated plainly |
| document structure | `<DocumentStructure>` |

Several of these currently take screen-level props; lift what you need rather than forking
them. A second `FindingCard` that drifts from the first is a worse outcome than a slightly
awkward prop.

Long results (forty references) collapse to a summary with a "show all" disclosure. The
agent was asked not to dump the parse result unprompted; the UI must not dump it either.

### Progress

Parse and review progress render as their own card, pinned above the composer while active
and then settled into the transcript at the point they completed.

- The bar is a **hairline that fills**, `h-px bg-cobalt`, exactly as `ProgressStrip` and the
  upload control do. Not a rounded track, not a spinner.
- The phase is always stated **in words** beside it. §5: a counter must never let
  in-progress read as complete.
- Parse: `Reconciling references · 62%` with the stage name from the backend, plus the
  filename.
- Review: the `verified N / total` counter in Bodoni at headline size, and — when the agent
  has been asked for detail — the honest secondary counters
  (`11 candidates discarded on the quote check · 6 abstracts unavailable`). Those numbers
  are what make a short findings list explicable instead of ambiguous.

### Confirmation

When `awaiting_confirmation` arrives, render the structured proposal — the real diff, the
real citation ledger, the real manifest — above the composer, with the agent's question
beneath it.

- **Yes / No are message senders.** They post canonical text ("Yes, commit those changes")
  into the conversation. The user can equally type it. Both paths go through the agent.
- **Orphaned anchors are not covered by a plain Yes.** Each unhoused citation renders as its
  own `<OrphanedAnchorCard>` with the three explicit choices — keep here / move to… /
  remove — showing the marker, the sentence it sat in, the best candidate home and the score
  it fell short by. Selecting them composes a message stating the decisions. There is no
  default, no "decide for me", and the Yes button stays disabled until every anchor has one.
  This is HR-5, and it is the one place in this flow where a bare "yes" is not enough.
- The pending proposal clears when the next user message is sent.

### The composer

- A textarea that grows to a few lines then scrolls. Enter sends, Shift+Enter newlines.
- Disabled while a turn is streaming, with a **Stop** control that calls `stopTurn` — a
  six-minute agent turn the user cannot interrupt is a trap.
- Above it, one `engraved-label` line naming what the agent can do right now, derived from
  live state, not a fixed string: while parsing, "The bibliography is still reconciling";
  after a review, nothing at all. Empty ivory is the intended state (§7 rule 4) — do not
  fill it with suggestion chips.

### Motion

180–240ms, `cubic-bezier(0.2, 0, 0.2, 1)`. Messages fade and rise 8px as they arrive; never
slide, never bounce. No typing-dots animation — a `<Seal kind="half">` beside the word
"Thinking" says the same thing in this typeface. Streaming text simply appears; do not
animate per character on top of a stream that is already progressive.
`prefers-reduced-motion` removes all of it, no exceptions.

### Accessibility

- The transcript is `aria-live="polite"` on the streaming region only — not the whole log,
  or a screen reader re-reads the conversation on every delta.
- Tool call states are announced by their text label, never by seal colour.
- Focus returns to the composer after a message is sent; the Stop control is keyboard
  reachable; confirmation buttons are real `<button>`s in DOM order after the proposal they
  act on.
- Focus rings: 2px `--ink-cobalt`, 2px offset. AA on every ink, 3:1 on every interactive
  edge. Measured, not assumed.

---

## 5. Failure states — §7 rule 8

These get the same design care as the success path. Each is visually distinct and says what
is true:

| Condition | What the screen says |
|---|---|
| API unreachable / keys missing | `<ConfigurationError>`, same as every other screen |
| Conversation not found | This document has no conversation — offer to start one, or to open the guided screens |
| Stream interrupted, reconnecting | Sepia, `Seal kind="half"`. Transcript stays; nothing already said is lost |
| Stream closed for good | Madder, `Seal kind="broken"`, the server's own reason verbatim |
| A turn failed mid-tool | The failed tool line renders broken with its error; the transcript stays usable and the composer re-enables |
| Token budget exhausted | Stated plainly: this document has spent its model budget, nothing was truncated to fit |
| Parse failed | The backend's reason, verbatim. Never "something went wrong" |
| Review failed | Madder card: *no claims were verified, so this is not a clean bill of health — it is a failed run.* Copy the framing `ReviewFeed`'s `EmptyState` already uses |

The distinction `ReviewFeed` draws between "nothing yet", "nothing found" and "failed"
applies here too. They are three different claims about the world and must never look alike.

---

## 6. Design-system upkeep

Add a **§5 entry for this screen** to `design/design-system.md`, in the voice of the
existing entries, covering: the printed-dialogue treatment, the opaque composer, tool calls
as sealed lines, and the reuse of the existing card suite. A screen that is not in §5 drifts
within two changes. Add the fork to the Upload entry as well, with the re-measured budget.

Add no new colour, no new font, no new radius, no new shadow. Everything this screen needs
is already in the token block at the top of `globals.css`, and Tailwind reads those tokens
through the `-rgb` channel forms — if you add a token, add both forms or opacity modifiers
silently emit invalid CSS with no build error.

---

## 7. Verification

1. `docker compose up --build`. Open **http://localhost:3000** and drop in a real PDF.
   Curl the page on `:3000`, not just the API on `:8000` — a working API behind a broken
   page is the failure mode this project keeps rediscovering.
2. Take the **conversational** fork. Confirm you land on the chat within a second, with
   parsing still running.
3. Confirm the agent's text arrives **progressively**. If it appears in one clump, the stream
   is being buffered — check that `EventSource` is pointed at FastAPI directly and not at a
   Next.js route.
4. Ask a question during parsing. Confirm the answer notes that references are not final.
5. Ask for a review. Confirm the agent describes the plan and waits; confirm the progress
   card ticks with real numbers; confirm findings render as `<FindingCard>`, not as prose.
6. Ask for an edit that orphans a citation. Confirm Yes stays disabled until every anchor is
   decided, and that the decision travels through a chat message.
7. Export, and confirm the placeholder disclosure appeared before the download.
8. Refresh mid-review. The transcript must repaint completely and then follow live, with no
   duplicated messages.
9. `NEXT_PUBLIC_USE_FIXTURES=1 pnpm dev` — the chat screen must work against the fixture
   client.
10. `pnpm typecheck && pnpm lint && pnpm build`. Then check the **served JS**, not just the
    source: a `NEXT_PUBLIC_` variable that inlines as `[SENSITIVE]` still returns a 200 page
    that quietly cannot reach the API.
11. Keyboard-only pass, and a `prefers-reduced-motion` pass.

Report what passed and what did not. A chat that streams beautifully and lets a citation be
dropped without a decision has failed the product, not partially succeeded.
