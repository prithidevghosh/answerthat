'use client';

import { useMemo, useState } from 'react';
import { ChangeCard } from '@/components/ChangeCard';
import { OrphanedAnchorCard } from '@/components/OrphanedAnchorCard';
import { RejectedOperationCard } from '@/components/RejectedOperationCard';
import { Plate } from '@/components/Plate';
import { Seal } from '@/components/Seal';
import { DoubleRule } from '@/components/Ornament';
import type { ChatConfirmation, OrphanDecision, OrphanOption } from '@/lib/api/types';
import type { SourceRecord } from '@/lib/contracts';

/**
 * The gate.
 *
 * **Yes and No are message senders.** They post canonical text into the
 * conversation and nothing else. Neither button calls `/change-sets/{id}/approve`,
 * and neither ever should: the moment this screen can commit without the agent,
 * the conversational flow is the deterministic one with extra steps, and the
 * runtime's own confirmation gate — which refuses a `confirm` tool that has not
 * had a user turn since its proposal — is bypassed rather than satisfied.
 *
 * **Orphaned anchors are the one thing a plain Yes cannot settle.** HR-5 says an
 * anchor that cannot be reattached is raised to the user and never dropped, so
 * every unhoused citation gets its own card with the three explicit choices, and
 * Yes stays disabled until each has one. There is no default and no "decide for
 * me". The backend refuses the commit too — this is the same rule stated twice,
 * on purpose, because a rule enforced in only one place is a rule that will be
 * enforced in neither once the other is refactored.
 */

export function Confirmation({
  confirmation,
  sources,
  busy,
  onSend,
}: {
  confirmation: ChatConfirmation;
  sources: Record<string, SourceRecord>;
  busy: boolean;
  onSend: (text: string) => void;
}) {
  const [decisions, setDecisions] = useState<Record<string, OrphanDecision>>({});

  const orphans = useMemo<OrphanOption[]>(
    () =>
      confirmation.kind === 'commit_change_set'
        ? confirmation.proposal.changes.flatMap((c) => c.orphans)
        : [],
    [confirmation],
  );

  const undecided = orphans.filter((o) => !decisions[o.anchor_id]);
  const blocked = undecided.length > 0;

  return (
    <section
      aria-labelledby="confirmation-heading"
      className="border-t border-fine bg-paper pt-6"
    >
      <h2 id="confirmation-heading" className="engraved-label text-cobalt">
        {HEADING[confirmation.kind]}
      </h2>

      <div className="mt-5 max-h-[46vh] overflow-y-auto pr-1">
        <Proposal confirmation={confirmation} sources={sources} />

        {orphans.length > 0 && (
          <div className="mt-8">
            <p className="inline-flex items-center gap-2 font-ui text-xs text-madder">
              <Seal kind="dangling" size={16} />
              {orphans.length} citation{orphans.length === 1 ? '' : 's'} cannot be placed
              automatically
            </p>
            <p className="measure mt-2 text-xs leading-relaxed text-secondary">
              Each of these needs a decision from you before anything is committed. Nothing is
              guessed and nothing is dropped.
            </p>
            <ul className="mt-5 space-y-6">
              {orphans.map((option) => (
                <OrphanedAnchorCard
                  key={option.anchor_id}
                  option={option}
                  sources={sources}
                  resolved={decisions[option.anchor_id] ?? null}
                  onResolve={(decision) =>
                    setDecisions((d) => ({ ...d, [decision.anchor_id]: decision }))
                  }
                  busy={busy}
                />
              ))}
            </ul>
          </div>
        )}
      </div>

      <DoubleRule className="mt-6" />

      {/*
        Real buttons, in DOM order after the proposal they act on, so a keyboard
        user reaches the decision after reading the thing being decided.
      */}
      <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-3">
        <button
          type="button"
          disabled={busy || blocked}
          onClick={() => onSend(composeYes(confirmation, orphans, decisions))}
          className="border border-verdigris/45 px-6 py-2.5 font-ui text-xs text-verdigris transition-colors duration-ink ease-ink hover:bg-verdigris/[0.07] disabled:opacity-40"
        >
          {AFFIRM[confirmation.kind]}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onSend(DECLINE[confirmation.kind])}
          className="border border-hair px-6 py-2.5 font-ui text-xs text-secondary transition-colors duration-ink ease-ink hover:border-strong hover:text-primary disabled:opacity-40"
        >
          No, not yet
        </button>

        <p className="font-ui text-2xs text-muted">
          {blocked ? (
            <span className="text-madder">
              {undecided.length} citation{undecided.length === 1 ? '' : 's'} above still need
              {undecided.length === 1 ? 's' : ''} a decision.
            </span>
          ) : (
            <>Either button sends a message — you can type the same thing instead.</>
          )}
        </p>
      </div>
    </section>
  );
}

const HEADING: Record<ChatConfirmation['kind'], string> = {
  commit_change_set: 'Waiting on you — proposed changes',
  export_latex: 'Waiting on you — export',
  revert_document: 'Waiting on you — revert',
  set_style: 'Waiting on you — citation style',
  unrecognised: 'Waiting on you',
};

const AFFIRM: Record<ChatConfirmation['kind'], string> = {
  commit_change_set: 'Yes, commit these',
  export_latex: 'Yes, render it',
  revert_document: 'Yes, revert',
  set_style: 'Yes, use that style',
  unrecognised: 'Yes, go ahead',
};

const DECLINE: Record<ChatConfirmation['kind'], string> = {
  commit_change_set: 'No — do not commit those changes yet.',
  export_latex: 'No — do not render the export yet.',
  revert_document: 'No — do not revert.',
  set_style: 'No — leave the citation style as it is.',
  unrecognised: 'No — not yet.',
};

/**
 * The message a Yes sends.
 *
 * Canonical, and it states the anchor decisions explicitly, because those
 * decisions are the *content* of the confirmation rather than metadata attached
 * to it. The agent reads this sentence and calls `commit_change_set` with the
 * decisions it names — the same thing a user who typed it by hand would get.
 */
function composeYes(
  confirmation: ChatConfirmation,
  orphans: OrphanOption[],
  decisions: Record<string, OrphanDecision>,
): string {
  if (confirmation.kind !== 'commit_change_set') {
    return `${AFFIRM[confirmation.kind]}.`;
  }

  const lines = orphans.map((option) => {
    const decision = decisions[option.anchor_id];
    const marker = option.marker ? `${option.marker} (${option.anchor_id})` : option.anchor_id;
    switch (decision.action) {
      case 'keep':
        return `Keep the citation ${marker} where it is.`;
      case 'move':
        return `Move the citation ${marker} to span ${decision.target_span_id ?? option.best_span_id}.`;
      case 'remove':
        return `Remove the citation ${marker} — I am approving that removal.`;
    }
  });

  return [
    `Yes, commit the changes in ${confirmation.proposal.change_set_id} against version ${confirmation.proposal.base_version}.`,
    ...lines,
  ].join(' ');
}

function Proposal({
  confirmation,
  sources,
}: {
  confirmation: ChatConfirmation;
  sources: Record<string, SourceRecord>;
}) {
  switch (confirmation.kind) {
    case 'commit_change_set':
      return (
        <>
          <ul className="space-y-6">
            {confirmation.proposal.changes.map((ec) => (
              <ChangeCard
                key={ec.change.change_id}
                evaluated={ec}
                sources={sources}
                readOnly
              />
            ))}
          </ul>
          {confirmation.proposal.rejected.length > 0 && (
            <div className="mt-6">
              <p className="engraved-label text-muted">Refused by the kernel</p>
              <ul className="mt-4 space-y-6">
                {confirmation.proposal.rejected.map((r, i) => (
                  <RejectedOperationCard key={i} rejected={r} />
                ))}
              </ul>
            </div>
          )}
        </>
      );

    case 'export_latex': {
      const placeholders = confirmation.proposal.placeholder_blocks.filter((p) => p.count > 0);
      return (
        <Plate accent="sepia" className="px-6 py-6">
          <p className="break-words font-mono text-base text-primary">
            {confirmation.proposal.filename}
          </p>
          <p className="mt-2 font-ui text-2xs text-muted">
            v{confirmation.proposal.version} · {confirmation.proposal.bibliography_entries}{' '}
            bibliography entries
            {confirmation.proposal.style_id && <> · {confirmation.proposal.style_id}</>}
          </p>
          {/* ADR-008, before the download rather than inside it. */}
          <p className="measure mt-4 text-xs leading-relaxed text-secondary">
            {placeholders.length === 0
              ? 'This paper has no figures, tables or equations, so nothing becomes a placeholder.'
              : `${placeholders
                  .map((p) => `${p.count} ${p.type}${p.count === 1 ? '' : 's'}`)
                  .join(', ')} become placeholders in the .tex — captions kept, content not.`}
          </p>
        </Plate>
      );
    }

    case 'revert_document':
      return (
        <Plate accent="sepia" className="px-6 py-6">
          <p className="measure text-base text-primary">
            Revert to version {confirmation.proposal.to_version}, from version{' '}
            {confirmation.proposal.current_version}.
          </p>
          <p className="measure mt-2 text-xs leading-relaxed text-secondary">
            Versions are append-only, so this writes a new version whose content matches the older
            one. Nothing in between is deleted.
          </p>
        </Plate>
      );

    case 'set_style':
      return (
        <Plate accent="cobalt" className="px-6 py-6">
          <p className="measure text-base text-primary">
            Set the citation style to <span className="font-mono">{confirmation.proposal.style_id}</span>
            {confirmation.proposal.current_style_id && (
              <>
                , replacing{' '}
                <span className="font-mono">{confirmation.proposal.current_style_id}</span>
              </>
            )}
            .
          </p>
        </Plate>
      );

    case 'unrecognised':
      // Not droppable. The agent is asking to do something, and a Yes button
      // over an invisible proposal is exactly what this gate exists to prevent.
      return (
        <Plate accent="sepia" className="px-6 py-6">
          <p className="inline-flex items-center gap-2 font-ui text-xs text-sepia">
            <Seal kind="half" size={16} />
            This screen has no card for “{confirmation.name || 'this request'}”
          </p>
          <p className="measure mt-2 text-xs leading-relaxed text-secondary">
            The proposal is shown as the API sent it, unedited. Read it before answering.
          </p>
          <pre className="mt-4 max-h-64 overflow-auto whitespace-pre-wrap break-words border border-hair bg-paper-deep px-4 py-3 font-mono text-2xs leading-relaxed text-secondary">
            {JSON.stringify(confirmation.proposal, null, 2)}
          </pre>
        </Plate>
      );
  }
}
