'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getClient } from '@/lib/api/client';
import type { UploadProgress } from '@/lib/api/types';
import { Seal } from './Seal';
import { Fleuron } from './Ornament';

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

      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        aria-describedby="upload-help"
        // An OPEN FRAME, not a filled panel. design-system.md §4 forbids a
        // scrim over the plate; the sky behind this is already at paper value,
        // so the frame needs no fill to be legible and the engraving shows
        // through it intact.
        className={`group relative flex h-[136px] w-full flex-col items-center justify-center gap-3 rounded border px-8 transition-colors duration-ink ease-ink ${
          dragging
            ? 'border-indigo bg-paper/70'
            : 'border-[var(--rule-strong)] hover:border-indigo hover:bg-paper/40'
        }`}
      >
        <span
          aria-hidden="true"
          className="text-indigo/70 transition-colors duration-ink group-hover:text-indigo"
        >
          <PlateIcon />
        </span>
        <span className="text-center">
          <span className="block font-display text-lg text-primary">
            {dragging ? 'Release to begin' : 'Drop your paper here'}
          </span>
          <span className="mt-1 block font-ui text-xs text-secondary">
            or <span className="underline decoration-indigo/40 underline-offset-4">choose a PDF</span>
          </span>
        </span>
      </button>

      {/*
        Kept inside the frame's own column and immediately beneath it, so the
        whole block stays within the plate's calm upper region. Nothing on this
        screen extends past the drop target.
      */}
      <p id="upload-help" className="mt-4 font-ui text-2xs text-muted">
        PDF, up to 50 MB. Nothing is published.
      </p>

      {state.kind === 'failed' && (
        <div
          role="alert"
          className="mt-6 flex w-full items-start gap-4 rounded border border-madder/40 bg-paper/85 px-6 py-5 text-left"
        >
          <span className="mt-px shrink-0 text-madder">
            <Seal kind="broken" size={18} />
          </span>
          <div>
            <p className="font-ui text-xs font-medium text-madder">{state.message}</p>
            {state.detail && (
              <p className="mt-1.5 text-xs leading-relaxed text-secondary">{state.detail}</p>
            )}
            <button
              type="button"
              onClick={() => {
                setState({ kind: 'idle' });
                inputRef.current?.click();
              }}
              className="mt-3 font-ui text-2xs text-indigo underline decoration-indigo/30 underline-offset-2 hover:decoration-indigo"
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
      <div className="flex h-[136px] w-full flex-col items-center justify-center gap-5 rounded border border-hair bg-paper/60 px-10">
        <Fleuron size={16} className="text-indigo/50" />

        <div className="w-full">
          <p className="text-center font-display text-lg text-primary">
            {STAGE_TEXT[progress.stage]}
          </p>

          {/* An indeterminate stage says so rather than animating a fake bar:
              we genuinely cannot report a fraction for server-side work. */}
          <div className="mt-4 h-px w-full bg-[var(--rule-hair)]">
            {pct !== null && (
              <div
                className="h-px bg-indigo transition-[width] duration-ink ease-ink"
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

/** A copperplate and burin — the press, not a cloud-arrow. */
function PlateIcon() {
  return (
    <svg
      width="38"
      height="38"
      viewBox="0 0 52 52"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.1"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="9.5" y="6.5" width="33" height="39" />
      <path d="M14 6.5v39M38 6.5v39" strokeDasharray="1.5 3" opacity="0.5" />
      <path d="M19 17h14M19 23h14M19 29h9" opacity="0.75" />
      <path d="M26 34.5v8M22.5 39l3.5 3.5 3.5-3.5" />
    </svg>
  );
}
