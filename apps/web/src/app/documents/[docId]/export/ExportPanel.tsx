'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Plate } from '@/components/Plate';
import { Seal } from '@/components/Seal';
import { RuleWithFleuron } from '@/components/Ornament';
import { getClient, USING_FIXTURES } from '@/lib/api/client';
import type { ExportManifest } from '@/lib/api/types';

const PLACEHOLDER_NOUN: Record<string, [string, string]> = {
  figure: ['figure', 'figures'],
  table: ['table', 'tables'],
  equation: ['equation', 'equations'],
};

/**
 * The ids the API actually serves, from `app/export/styles.py`.
 *
 * Kept here rather than reusing `styleName` from StyleBanner, whose table is
 * keyed on `acm-sig-proceedings` — the *filename* — where the API's id is `acm`,
 * so ACM rendered as the raw slug. This list doubles as the change control's
 * options, and an option whose id the API rejects is worse than a bare slug.
 */
const STYLE_LABEL: Record<string, string> = {
  apa: 'APA 7th edition',
  ieee: 'IEEE',
  acm: 'ACM (SIG Proceedings)',
  nature: 'Nature',
  'chicago-author-date': 'Chicago (author–date)',
  vancouver: 'Vancouver',
};

export function ExportPanel({ docId, manifest }: { docId: string; manifest: ExportManifest }) {
  const [pressed, setPressed] = useState(false);

  const placeholders = manifest.placeholder_blocks.filter((p) => p.count > 0);
  const totalPlaceholders = placeholders.reduce((n, p) => n + p.count, 0);

  return (
    <main id="main" className="relative z-10 content-column py-16">
      <p className="font-ui text-2xs uppercase tracking-[0.14em] text-muted">Export</p>
      <h1 className="mt-2 font-display text-3xl text-primary">Take your paper back</h1>

      <div className="mt-12 grid gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,320px)]">
        <div>
          <Plate className="px-8 py-10">
            <div className="measure">
              <p className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">
                Revised manuscript
              </p>
              <p className="mt-2 break-words font-mono text-base text-primary">
                {manifest.filename}
              </p>

              <dl className="mt-8 grid grid-cols-2 gap-x-8 gap-y-5 font-ui text-2xs">
                <Stat label="Version" value={`v${manifest.version}`} />
                <Stat
                  label="Bibliography"
                  value={`${manifest.bibliography_entries} entries`}
                />
                <Stat
                  label="Citation style"
                  value={manifest.style_id ? STYLE_LABEL[manifest.style_id] ?? manifest.style_id : 'None recorded'}
                />
                <Stat label="Format" value="LaTeX (.tex)" />
              </dl>

              {/*
                ADR-030. Export no longer waits on a style question — detection's
                closest match is used. But "closest match" is not "identified", and
                the user is the only one who can tell us we got it wrong, so the
                close call is stated here rather than left in a log.
              */}
              <StyleControl
                docId={docId}
                styleId={manifest.style_id}
                uncertain={manifest.style_uncertain}
              />

              <div className="mt-10">
                {/*
                  A button that cannot succeed is worse than no button: the user
                  clicks, the render refuses, and the refusal arrives as a broken
                  download rather than as the decision it actually is. So when the
                  API says the export is blocked, we show the reason and the way
                  out instead (HR-3).
                */}
                {manifest.exportable ? (
                  <>
                    <a
                      href={getClient().exportUrl(docId)}
                      download={manifest.filename}
                      onClick={(e) => {
                        if (USING_FIXTURES) {
                          // Fixture mode has no file to hand over, and offering a
                          // download that silently does nothing would be its own
                          // small dishonesty.
                          e.preventDefault();
                        }
                        setPressed(true);
                        window.setTimeout(() => setPressed(false), 600);
                      }}
                      className={`inline-flex items-center gap-3 rounded border border-indigo/45 bg-plate px-7 py-3.5 font-ui text-xs text-indigo transition-colors duration-ink ease-ink hover:bg-indigo/[0.06] ${
                        pressed ? 'animate-impress' : ''
                      }`}
                    >
                      <Seal kind="filled" size={17} />
                      Download the revised .tex
                    </a>

                    {USING_FIXTURES && (
                      <p className="mt-3 font-ui text-2xs text-sepia">
                        Fixture mode — there is no real document to download.
                      </p>
                    )}
                  </>
                ) : (
                  <div className="rounded border border-sepia/40 bg-sepia/[0.05] px-6 py-5">
                    <span className="inline-flex items-center gap-2 font-ui text-xs font-medium text-sepia">
                      <Seal kind="half" size={17} />
                      This export is not ready yet
                    </span>
                    <p className="measure mt-3 text-xs leading-relaxed text-secondary">
                      {manifest.blocked_reason ??
                        'The API reported that this document cannot be rendered yet.'}
                    </p>
                    {/*
                      The chooser is already on this screen, above. Sending the user
                      to the parse page for it was a dead end in the one case that
                      lands here: an ingest report the API no longer holds takes the
                      parse screen down with it, picker included.
                    */}
                    <p className="mt-4 font-ui text-2xs text-muted">
                      Use the style buttons above — the export runs as soon as one is set.
                    </p>
                  </div>
                )}
              </div>
            </div>
          </Plate>

          <RuleWithFleuron className="my-12" />

          {/*
            ADR-008 is a *stated* scope cut, so the user meets it here in plain
            words rather than discovering it in their LaTeX. This panel gets the
            same care as the download itself — it is the honest part.
          */}
          <section aria-labelledby="scope-heading">
            <h2
              id="scope-heading"
              className="inline-flex items-center gap-2 font-ui text-xs font-medium text-sepia"
            >
              <Seal kind="frame" size={17} />
              What this export does not contain
            </h2>

            <p className="measure mt-4 text-base leading-relaxed text-secondary">
              Your sections, paragraphs, citations and bibliography survive the round trip exactly.
              Figures, tables and equations do not: reconstructing them faithfully from a PDF is its
              own research problem, so each one becomes a visible placeholder block carrying its
              original caption.
            </p>

            {totalPlaceholders > 0 && (
              <>
                <p className="mt-6 font-ui text-2xs uppercase tracking-[0.12em] text-muted">
                  In this document
                </p>
                <ul className="mt-3 space-y-2">
                  {placeholders.map((p) => {
                    const [one, many] = PLACEHOLDER_NOUN[p.type] ?? [p.type, `${p.type}s`];
                    return (
                      <li
                        key={p.type}
                        className="border-l-2 border-sepia/40 py-1 pl-4 text-base text-primary"
                      >
                        {p.count} {p.count === 1 ? one : many}{' '}
                        <span className="text-secondary">
                          {p.count === 1 ? 'is a placeholder' : 'are placeholders'}
                        </span>
                      </li>
                    );
                  })}
                </ul>
                <p className="measure mt-5 text-xs leading-relaxed text-secondary">
                  You will see them in the .tex as clearly marked blocks. Nothing is silently
                  dropped, and nothing pretends to be a fidelity we do not have.
                </p>
              </>
            )}
          </section>
        </div>

        <aside>
          <h2 className="font-ui text-2xs uppercase tracking-[0.14em] text-muted">
            What survives exactly
          </h2>
          <ul className="mt-6 space-y-4">
            {[
              'Every section heading, in order',
              'Every paragraph, unchanged unless you approved a change',
              'Every in-text citation marker',
              'Every bibliography entry, rendered by Pandoc from the same .csl file used for preview',
            ].map((item) => (
              <li key={item} className="flex gap-3">
                <span className="mt-0.5 shrink-0 text-indigo">
                  <Seal kind="filled" size={14} />
                </span>
                <span className="text-xs leading-relaxed text-secondary">{item}</span>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </main>
  );
}

/**
 * States which style the export will render in, and lets the user overrule it.
 *
 * Only speaks up when it has something to say: a confidently detected style is
 * already shown in the stat block above, and repeating it as a warning would
 * train the user to ignore the times it matters.
 */
function StyleControl({
  docId,
  styleId,
  uncertain,
}: {
  docId: string;
  styleId: string | null;
  uncertain: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function choose(next: string) {
    setSaving(next);
    setError(null);
    try {
      await getClient().chooseStyle(docId, next);
      setOpen(false);
      // The manifest is fetched server-side, so re-render the route rather than
      // patching local state — otherwise the download link and the label could
      // disagree about which style the file is in.
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  }

  if (!uncertain && !open && styleId) {
    return (
      <p className="mt-5 font-ui text-2xs text-muted">
        Rendering in {STYLE_LABEL[styleId] ?? styleId}.{' '}
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="underline decoration-hair underline-offset-2 transition-colors duration-ink ease-ink hover:text-primary"
        >
          Change
        </button>
      </p>
    );
  }

  return (
    <div className="mt-6 rounded border border-sepia/40 bg-sepia/[0.05] px-5 py-4">
      {uncertain && styleId && (
        <>
          <span className="inline-flex items-center gap-2 font-ui text-xs font-medium text-sepia">
            <Seal kind="half" size={17} />
            Exporting in {STYLE_LABEL[styleId] ?? styleId} — this was a close call
          </span>
          <p className="measure mt-3 text-xs leading-relaxed text-secondary">
            We render your references through each candidate style and compare the result to the
            raw strings in your paper. Two styles scored within 0.05 of each other, so we used the
            closer one. Your in-text citations read the same either way; the difference is in the
            reference list. If it is wrong, change it here.
          </p>
        </>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {Object.entries(STYLE_LABEL).map(([id, label]) => (
          <button
            key={id}
            type="button"
            disabled={saving !== null || id === styleId}
            onClick={() => choose(id)}
            className={`rounded border px-4 py-2 font-ui text-2xs transition-colors duration-ink ease-ink disabled:opacity-50 ${
              id === styleId
                ? 'border-indigo/45 bg-indigo/[0.06] text-indigo'
                : 'border-sepia/40 text-primary hover:bg-sepia/[0.07]'
            }`}
          >
            {saving === id ? 'Saving…' : label}
            {id === styleId && <span className="ml-2 text-muted">in use</span>}
          </button>
        ))}
      </div>

      {error && <p className="mt-3 font-ui text-2xs text-madder">{error}</p>}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="uppercase tracking-[0.12em] text-muted">{label}</dt>
      <dd className="mt-1 text-base text-primary">{value}</dd>
    </div>
  );
}
