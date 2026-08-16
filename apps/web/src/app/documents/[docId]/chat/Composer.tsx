'use client';

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Seal } from '@/components/Seal';

const MAX_HEIGHT = 168; // ~6 lines, then it scrolls.

/**
 * The instrument at the foot of the desk.
 *
 * Enter sends, Shift+Enter makes a newline. It grows to a few lines and then
 * scrolls rather than pushing the transcript off the top of the screen.
 *
 * **Stop is not optional.** A review runs for minutes and an agent turn can sit
 * inside one; a six-minute turn the user cannot interrupt is a trap, and the
 * control is keyboard reachable for the same reason it exists at all.
 *
 * The hint line above is derived from live state and is empty most of the time.
 * Empty ivory is the intended state — the alternative is a row of suggestion
 * chips telling the user what to want.
 */
export const Composer = forwardRef<
  HTMLTextAreaElement,
  {
    busy: boolean;
    hint: string | null;
    onSend: (text: string) => void;
    onStop: () => void;
  }
>(function Composer({ busy, hint, onSend, onStop }, ref) {
  const [text, setText] = useState('');
  const innerRef = useRef<HTMLTextAreaElement>(null);
  const stopRef = useRef<HTMLButtonElement>(null);
  const wasBusy = useRef(false);
  useImperativeHandle(ref, () => innerRef.current as HTMLTextAreaElement, []);

  /**
   * Focus has to be handed over, not just "returned to the composer".
   *
   * Sending disables the textarea, and a disabled element cannot hold focus —
   * the browser drops it to `<body>`, which strands a keyboard user with no
   * way back and no way to press Stop. So focus moves to Stop when the turn
   * starts and back to the composer when it ends. Neither move happens if the
   * user has deliberately put focus somewhere real in the meantime: the guard
   * is that `activeElement` is the body or the control that is about to
   * disappear.
   */
  useEffect(() => {
    const active = document.activeElement;
    const loose = !active || active === document.body;

    if (busy && !wasBusy.current) {
      if (loose || active === innerRef.current) stopRef.current?.focus();
    } else if (!busy && wasBusy.current) {
      if (loose) innerRef.current?.focus();
    }
    wasBusy.current = busy;
  }, [busy]);

  const grow = (el: HTMLTextAreaElement) => {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  };

  const submit = () => {
    const trimmed = text.trim();
    if (trimmed === '' || busy) return;
    onSend(trimmed);
    setText('');
    if (innerRef.current) {
      innerRef.current.style.height = 'auto';
    }
  };

  return (
    <form
      className="mt-6"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      {hint && (
        <p className="engraved-label mb-3 text-muted" aria-live="polite">
          {hint}
        </p>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <label htmlFor="chat-message" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-message"
          ref={innerRef}
          rows={1}
          value={text}
          disabled={busy}
          onChange={(e) => {
            setText(e.target.value);
            grow(e.target);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={busy ? 'Working…' : 'Ask about this paper, or say what to change'}
          className="min-h-[48px] min-w-0 flex-1 resize-none overflow-y-auto border border-[var(--rule-strong)] bg-leaf px-4 py-3 font-body text-base leading-relaxed text-primary placeholder:text-muted focus:border-cobalt disabled:opacity-60"
          style={{ maxHeight: MAX_HEIGHT }}
        />

        {busy ? (
          <button
            type="button"
            ref={stopRef}
            onClick={onStop}
            className="h-fit border border-madder/45 px-6 py-3 font-ui text-xs text-madder transition-colors duration-ink ease-ink hover:bg-madder/[0.07]"
          >
            <span className="inline-flex items-center gap-2">
              <Seal kind="broken" size={15} />
              Stop
            </span>
          </button>
        ) : (
          <button
            type="submit"
            disabled={text.trim() === ''}
            className="h-fit border border-cobalt/45 px-6 py-3 font-ui text-xs text-cobalt transition-colors duration-ink ease-ink hover:bg-cobalt/[0.06] disabled:opacity-40"
          >
            Send
          </button>
        )}
      </div>

      <p className="mt-2 font-ui text-2xs text-muted">
        Enter sends · Shift+Enter for a new line · nothing is written to your paper without your
        say-so
      </p>
    </form>
  );
});
