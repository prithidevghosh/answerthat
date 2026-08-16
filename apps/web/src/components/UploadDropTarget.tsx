'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getClient } from '@/lib/api/client';
import type { UploadProgress } from '@/lib/api/types';
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
  | { kind: 'working'; progress: UploadProgress }
  | { kind: 'failed'; message: string; detail: string | null };

export function UploadDropTarget() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<State>({ kind: 'idle' });
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);

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

      setState({
        kind: 'working',
        progress: { stage: 'uploading', fraction: 0, detail: file.name },
      });

      try {
        const { doc_id } = await getClient().uploadPdf(file, (progress) =>
          setState({ kind: 'working', progress }),
        );
        router.push(`/documents/${doc_id}/parse`);
      } catch (err) {
        setState({
          kind: 'failed',
          message: 'The upload did not complete.',
          detail: err instanceof Error ? err.message : String(err),
        });
      }
    },
    [router],
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
    return <Working progress={state.progress} />;
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

function Working({ progress }: { progress: UploadProgress }) {
  const pct = progress.fraction === null ? null : Math.round(progress.fraction * 100);

  return (
    <div className="flex w-full flex-col items-center" aria-live="polite">
      {/* Same footprint as the cartouche it replaces, so the threshold does not
          jump when the upload starts. */}
      <div className="relative flex h-[78px] w-[clamp(304px,27vw,364px)] flex-col items-center justify-center border border-hair bg-paper/55 px-6">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute -inset-[5px] border border-hair"
        />

        <div className="w-full">
          <p className="text-center font-display text-base leading-tight tracking-[-0.01em] text-primary">
            {STAGE_TEXT[progress.stage]}
          </p>

          {/* An indeterminate stage says so rather than animating a fake bar:
              we genuinely cannot report a fraction for server-side work. */}
          <div className="mt-4 h-px w-full bg-[var(--rule-hair)]">
            {pct !== null && (
              <div
                className="h-px bg-cobalt transition-[width] duration-ink ease-ink"
                style={{ width: `${pct}%` }}
              />
            )}
          </div>

          <p className="mt-3 text-center font-ui text-2xs text-muted">
            {pct !== null ? `${pct}%` : 'This step does not report progress'}
            {progress.detail && <> · {progress.detail}</>}
          </p>
        </div>
      </div>

      <p className="mt-4 font-ui text-2xs text-muted">
        Parsing takes about a minute for a typical paper.
      </p>
    </div>
  );
}
