'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getClient } from '@/lib/api/client';
import { FLOW_PATH, rememberFlow, type Flow } from '@/lib/flow';
import type { UploadAccepted, UploadProgress } from '@/lib/api/types';
import { Seal } from './Seal';

const MAX_BYTES = 50 * 1024 * 1024;

const STAGE_TEXT: Record<UploadProgress['stage'], string> = {
  uploading: 'Sending your paper',
  extracting: 'Reading the document structure',
  parsing: 'Segmenting references',
  resolving: 'Reconciling against Crossref, Semantic Scholar and OpenAlex',
  complete: 'Ready',
};

type State =
  | { kind: 'idle' }
  | { kind: 'working'; progress: UploadProgress; chosen: Flow | null }
  | { kind: 'failed'; message: string; detail: string | null };

export function UploadDropTarget() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<State>({ kind: 'idle' });
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);

  /**
   * The 202, in flight.
   *
   * The bytes go the moment the file is dropped — waiting for the user to pick
   * a route would spend the whole upload doing nothing — so the promise is held
   * here and the choice decides what happens when it lands. Both routes await
   * the same one; neither uploads twice.
   */
  const acceptedRef = useRef<Promise<UploadAccepted> | null>(null);
  const fileRef = useRef<File | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const fail = useCallback((message: string, detail: string | null) => {
    setState({ kind: 'failed', message, detail });
    acceptedRef.current = null;
  }, []);

  /**
   * The fork.
   *
   * Guided is `uploadPdf(...).then(waitForParse)` — the old single call, split
   * in two and put back together, so the parse inspector is still never opened
   * before `/parse` will answer it.
   *
   * Conversational navigates on the 202 on purpose. The agent's first job is to
   * narrate the parse, and its screen is built to show an ingest in progress;
   * arriving after the parse finished would be arriving after the interesting
   * part.
   */
  const choose = useCallback(
    async (flow: Flow) => {
      const pending = acceptedRef.current;
      const file = fileRef.current;
      if (!pending) return;

      setState((s) =>
        s.kind === 'working' ? { ...s, chosen: flow } : s,
      );

      try {
        const accepted = await pending;
        rememberFlow(accepted.doc_id, flow);

        if (flow === 'conversational') {
          router.push(FLOW_PATH.conversational(accepted.doc_id));
          return;
        }

        await getClient().waitForParse(
          accepted.doc_id,
          (progress) =>
            setState({
              kind: 'working',
              // `waitForParse` has a doc id, not a File, so it cannot name the
              // paper. The name is ours to add back.
              progress: { ...progress, detail: progress.detail ?? file?.name ?? null },
              chosen: flow,
            }),
          abortRef.current?.signal,
        );
        router.push(FLOW_PATH.guided(accepted.doc_id));
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        fail(
          'The upload did not complete.',
          err instanceof Error ? err.message : String(err),
        );
      }
    },
    [router, fail],
  );

  const begin = useCallback(
    async (file: File) => {
      // Validate before we send: a rejection the user can act on immediately
      // beats a round trip that fails for a reason we already knew.
      if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
        setState({
          kind: 'failed',
          message: 'That file is not a PDF.',
          detail: `Answerthat reads the PDF of a paper. You gave it “${file.name}”.`,
        });
        return;
      }
      if (file.size > MAX_BYTES) {
        setState({
          kind: 'failed',
          message: 'That PDF is larger than 50 MB.',
          detail: `“${file.name}” is ${(file.size / 1024 / 1024).toFixed(1)} MB. If it is a scan, a text-based export will parse far better.`,
        });
        return;
      }
      if (file.size === 0) {
        setState({
          kind: 'failed',
          message: 'That file is empty.',
          detail: `“${file.name}” is 0 bytes.`,
        });
        return;
      }

      fileRef.current = file;
      abortRef.current = new AbortController();
      setState({
        kind: 'working',
        progress: { stage: 'uploading', fraction: 0, detail: file.name },
        chosen: null,
      });

      // Sent immediately. The choice below is offered while these bytes are in
      // flight; it decides what happens when the 202 lands, not whether it is
      // sent. A `catch` is attached here as well as at the await in `choose`,
      // because a rejection with no handler between the drop and the pick is an
      // unhandled promise rejection in the console and a screen that just sits
      // there.
      const pending = getClient().uploadPdf(
        file,
        (progress) => setState((s) => (s.kind === 'working' ? { ...s, progress } : s)),
        abortRef.current.signal,
      );
      acceptedRef.current = pending;
      pending.catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        fail('The upload did not complete.', err instanceof Error ? err.message : String(err));
      });
    },
    [fail],
  );

  // Whole-window drag, so the target is the screen rather than a small rectangle.
  useEffect(() => {
    const over = (e: DragEvent) => {
      if (!e.dataTransfer?.types.includes('Files')) return;
      e.preventDefault();
    };
    const enter = (e: DragEvent) => {
      if (!e.dataTransfer?.types.includes('Files')) return;
      e.preventDefault();
      dragDepth.current += 1;
      setDragging(true);
    };
    const leave = () => {
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDragging(false);
    };
    const drop = (e: DragEvent) => {
      if (!e.dataTransfer?.types.includes('Files')) return;
      e.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      const file = e.dataTransfer?.files?.[0];
      if (file) void begin(file);
    };
    window.addEventListener('dragover', over);
    window.addEventListener('dragenter', enter);
    window.addEventListener('dragleave', leave);
    window.addEventListener('drop', drop);
    return () => {
      window.removeEventListener('dragover', over);
      window.removeEventListener('dragenter', enter);
      window.removeEventListener('dragleave', leave);
      window.removeEventListener('drop', drop);
    };
  }, [begin]);

  if (state.kind === 'working') {
    return <Fork progress={state.progress} chosen={state.chosen} onChoose={choose} />;
  }

  return (
    <div className="flex flex-col items-center">
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        className="sr-only"
        id="paper-file"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void begin(file);
          e.target.value = '';
        }}
      />

      {/*
        A CARTOUCHE, not a dropzone.

        This was a 540x148 hollow rectangle — a void sitting on the engraving,
        and a foreign object on a plate. It does not need to be large: the drag
        listeners above are bound to `window`, so the whole screen is already
        the drop area and this element only has to be the affordance and the
        click target. Sized as a deliberate object rather than a field, it reads
        as the framed label plaque an engraving actually contains, it stops
        competing with the wordmark, and the ~70px it gives back is what keeps
        the whole block inside the plate's open field on a short screen.

        Still an OPEN frame, never a filled panel: §4 forbids a scrim over the
        plate, and the field behind this is already at paper value.
      */}
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        aria-describedby="upload-help"
        className={`group relative flex h-[74px] w-[clamp(304px,27vw,364px)] flex-col items-center justify-center border px-6 transition-colors duration-ink ease-ink ${
          dragging
            ? 'border-cobalt bg-paper/70'
            : 'border-[var(--rule-strong)] hover:border-cobalt hover:bg-paper/35'
        }`}
      >
        {/* The outer line of the plate border. Two rules, one gap — the frame an
            engraver cuts around an image. Drawn as a sibling rather than an
            `outline` so it never fights the focus ring. */}
        <span
          aria-hidden="true"
          className={`pointer-events-none absolute -inset-[5px] border transition-colors duration-ink ${
            dragging ? 'border-cobalt/50' : 'border-cobalt/25 group-hover:border-cobalt/40'
          }`}
        />

        {/*
          Centred and typographic, with no icon.

          The icon sat to the left of a two-line left-aligned block, which put
          a long line over a short one and left a void in the bottom-right of
          the plaque — the group measured centred and still read left-heavy.
          A cartouche on an engraving is a piece of set type in a frame, so
          this is set type in a frame: two centred lines, symmetric about the
          same axis as the wordmark above.
        */}
        <span className="block font-display text-lg leading-tight tracking-[-0.01em] text-primary">
          {dragging ? 'Release to begin' : 'Drop your paper here'}
        </span>
        <span className="mt-1 block font-ui text-2xs text-secondary">
          or{' '}
          <span className="underline decoration-cobalt/40 underline-offset-4 group-hover:decoration-cobalt">
            choose a PDF
          </span>
        </span>
      </button>

      {/*
        The lowest line on the threshold, and so the one closest to the edge of
        the plate's open field — it is the first thing to end up over foliage if
        the block above it grows. It carries real terms, so it is set in the
        secondary ink rather than muted — 7.24:1 on paper against 5.29. Do not
        lighten it back. It stays at the standard engraved-label size so it is
        narrower than the cartouche it captions; at a step larger it ran wider
        than the plaque, which reads as a heading rather than a footnote.
      */}
      <p
        id="upload-help"
        className="engraved-label mt-4 text-secondary [@media(min-height:940px)]:mt-5"
      >
        PDF · up to 50 MB · nothing is published
      </p>

      {state.kind === 'failed' && (
        <div
          role="alert"
          // Fully opaque, not bg-leaf/90. This is the one element on the
          // threshold that legitimately extends past the plate's open field,
          // because an error must be shown wherever it happens — so it brings
          // its own paper rather than relying on the field being light. A card
          // with its own ground is the allowed way to do that; bare text over
          // the engraving is not (§4 rule 2).
          className="relative mt-6 flex w-full items-start gap-4 border border-hair bg-leaf py-5 pl-6 pr-6 text-left"
        >
          <span aria-hidden="true" className="absolute inset-y-0 left-0 w-[2px] bg-madder" />
          <span className="mt-px shrink-0 text-madder">
            <Seal kind="broken" size={18} />
          </span>
          <div>
            <p className="engraved-label text-madder">{state.message}</p>
            {state.detail && (
              <p className="mt-1.5 text-xs leading-relaxed text-secondary">{state.detail}</p>
            )}
            <button
              type="button"
              onClick={() => {
                setState({ kind: 'idle' });
                inputRef.current?.click();
              }}
              className="mt-3 font-ui text-2xs text-cobalt underline decoration-cobalt/30 underline-offset-2 hover:decoration-cobalt"
            >
              Choose another file
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const ROUTES: { flow: Flow; name: string; line: string }[] = [
  { flow: 'guided', name: 'Guided', line: 'You drive' },
  { flow: 'conversational', name: 'Conversational', line: 'An assistant drives' },
];

/**
 * The fork, offered while the bytes are in flight.
 *
 * Two cartouches, in the same language as the one they replace: an open double
 * rule, square corners, set type, no icon and no fill. Not two filled buttons —
 * a filled control is a foreign object on an engraving, and the reason the drop
 * target is a cartouche in the first place applies twice over to a pair of them.
 * Not a toggle either: these are two named places to go, not one setting.
 *
 * THE BUDGET (design-system.md §4, and the note in `app/page.tsx`). Everything
 * on the threshold must end above 50% of viewport height, where the plate's
 * luminance falls off a cliff — 7.46:1 down to 50%, 3.10:1 by 55%, 1.37:1 by
 * 65%. This block is 140px tall, which makes it the *tallest state the
 * threshold has* — taller than the 105px cartouche-and-caption it replaces — so
 * it is the one that governs now. Re-measured in a browser, not derived:
 *
 *   viewport    cliff   block ends at   spare
 *   1024x640     320         314          6
 *   1280x720     360         342         18
 *   1366x768     384         360         24
 *   1440x900     450         417         33
 *   1920x1080    540         485         55
 *
 * Six pixels at 1024x640 is the real margin, and it is why the two gaps below
 * are on the tight end of the spacing scale rather than stepping up with
 * viewport height as the title page above them does.
 *
 * **If you add a line here, re-measure in a browser.** The route caption is the
 * first thing to cut — the cartouches' own second lines already name the
 * difference. Crushing the spacing is the wrong trade.
 */
function Fork({
  progress,
  chosen,
  onChoose,
}: {
  progress: UploadProgress;
  chosen: Flow | null;
  onChoose: (flow: Flow) => void;
}) {
  const pct = progress.fraction === null ? null : Math.round(progress.fraction * 100);

  return (
    <div className="flex w-full flex-col items-center">
      <fieldset className="w-full border-0 p-0">
        <legend className="sr-only">How would you like to work on this paper?</legend>

        {/* Stacked below 900px, where the frontispiece is a foot band and the
            content runs on plain ivory — there is room, and two 250px plaques
            side by side would be too narrow to set type in. */}
        <div className="flex flex-col items-center gap-3 min-[900px]:flex-row min-[900px]:justify-center min-[900px]:gap-4">
          {ROUTES.map((route) => {
            const isChosen = chosen === route.flow;
            return (
              <button
                key={route.flow}
                type="button"
                disabled={chosen !== null}
                onClick={() => onChoose(route.flow)}
                className={`group relative flex h-[74px] w-full flex-col items-center justify-center border px-5 transition-colors duration-ink ease-ink min-[900px]:w-[clamp(196px,18vw,250px)] ${
                  isChosen
                    ? 'border-cobalt'
                    : chosen
                      ? 'border-[var(--rule-hair)] opacity-45'
                      : 'border-[var(--rule-strong)] hover:border-cobalt hover:bg-paper/35'
                }`}
              >
                {/* The outer line of the plate border — two rules, one gap. */}
                <span
                  aria-hidden="true"
                  className={`pointer-events-none absolute -inset-[5px] border transition-colors duration-ink ${
                    isChosen
                      ? 'border-cobalt/50'
                      : 'border-cobalt/25 group-hover:border-cobalt/40'
                  }`}
                />
                <span className="block font-display text-lg leading-tight tracking-[-0.01em] text-primary">
                  {route.name}
                </span>
                <span className="mt-1 block font-ui text-2xs text-secondary">{route.line}</span>
              </button>
            );
          })}
        </div>
      </fieldset>

      {/*
        One line naming the difference, in the caption's own register. It is
        the engraved-label size for the same reason the upload caption is:
        a step larger and it runs wider than the plaques it captions, which
        reads as a heading rather than a footnote.
      */}
      <p className="engraved-label mt-3 text-center text-secondary">
        {chosen === 'conversational'
          ? 'Opening the conversation'
          : chosen === 'guided'
            ? 'Waiting for the parse to finish'
            : 'One screen per step, or one conversation'}
      </p>

      {/* The upload, underneath. A hairline that fills — never a rounded track,
          never a spinner — and an indeterminate stage says so rather than
          animating a fake bar. */}
      <div className="mt-3 w-full" aria-live="polite">
        <div className="h-px w-full bg-[var(--rule-hair)]">
          {pct !== null && (
            <div
              className="h-px bg-cobalt transition-[width] duration-ink ease-ink"
              style={{ width: `${pct}%` }}
            />
          )}
        </div>
        <p className="mt-2 text-center font-ui text-2xs text-muted">
          {STAGE_TEXT[progress.stage]}
          {' · '}
          {pct !== null ? `${pct}%` : 'this step does not report progress'}
        </p>
      </div>
    </div>
  );
}
