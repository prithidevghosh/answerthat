'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Plate } from '@/components/Plate';
import { Seal } from '@/components/Seal';
import { RuleWithFleuron } from '@/components/Ornament';
import { getClient, USING_FIXTURES } from '@/lib/api/client';
import { styleName } from '@/components/StyleBanner';
import type { ExportManifest } from '@/lib/api/types';

const PLACEHOLDER_NOUN: Record<string, [string, string]> = {
  figure: ['figure', 'figures'],
  table: ['table', 'tables'],
  equation: ['equation', 'equations'],
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
                  // Detection ran; it declined to pick between two near-tied
                  // candidates. "Not detected" would misreport that as a failure.
                  value={manifest.style_id ? styleName(manifest.style_id) : 'Not chosen yet'}
                />
                <Stat label="Format" value="LaTeX (.tex)" />
              </dl>

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
                    <Link
                      href={`/documents/${docId}/parse`}
                      className="mt-5 inline-flex items-center gap-3 rounded border border-sepia/45 bg-plate px-6 py-3 font-ui text-xs text-primary transition-colors duration-ink ease-ink hover:bg-sepia/[0.07]"
                    >
                      Choose a citation style
                    </Link>
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

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="uppercase tracking-[0.12em] text-muted">{label}</dt>
      <dd className="mt-1 text-base text-primary">{value}</dd>
    </div>
  );
}
