'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ChangeCard, type Decision } from '@/components/ChangeCard';
import { OrphanedAnchorCard } from '@/components/OrphanedAnchorCard';
import { RejectedOperationCard } from '@/components/RejectedOperationCard';
import { Plate } from '@/components/Plate';
import { Seal } from '@/components/Seal';
import { Fleuron } from '@/components/Ornament';
import { getClient } from '@/lib/api/client';
import type { CommandResult, CommitResult, OrphanDecision, OrphanOption } from '@/lib/api/types';
import type { SourceRecord } from '@/lib/contracts';

type Phase = 'idle' | 'planning' | 'ready' | 'failed';

const EXAMPLES = [
  'Shorten the related work section by a third.',
  'Add citations to the claim about wall-clock latency.',
  'Rewrite the discussion to foreground the retrieval results.',
];

export function EditConsole({ docId }: { docId: string }) {
  const [command, setCommand] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [result, setResult] = useState<CommandResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [orphanDecisions, setOrphanDecisions] = useState<Record<string, OrphanDecision>>({});
  const [sources, setSources] = useState<Record<string, SourceRecord>>({});
  const [busy, setBusy] = useState(false);
  const [commit, setCommit] = useState<CommitResult | null>(null);

  /**
   * Orphans hang off the change that unhoused them, so the console flattens them
   * for display and carries the change_id along: a decision only becomes binding
   * if its change is approved, and the commit has to know which is which.
   */
  const orphans = useMemo<{ changeId: string; option: OrphanOption }[]>(
    () =>
      (result?.changes ?? []).flatMap((c) =>
        c.orphans.map((option) => ({ changeId: c.change.change_id, option })),
      ),
    [result],
  );

  const neededIds = useMemo(() => {
    if (!result) return [];
    const ids = new Set<string>();
    result.changes.forEach((c) => {
      c.change.new_source_ids.forEach((s) => ids.add(s));
      c.diff.citations.anchors.forEach((a) => {
        a.source_ids_before.forEach((s) => ids.add(s));
        a.source_ids_after.forEach((s) => ids.add(s));
      });
      c.orphans.forEach((o) => o.source_ids.forEach((s) => ids.add(s)));
    });
    return [...ids].filter((id) => !(id in sources));
  }, [result, sources]);

  useEffect(() => {
    if (neededIds.length === 0) return;
    let live = true;
    Promise.allSettled(neededIds.map((id) => getClient().getSource(id))).then((settled) => {
      if (!live) return;
      const next: Record<string, SourceRecord> = {};
      settled.forEach((s) => {
        if (s.status === 'fulfilled') next[s.value.source_id] = s.value;
      });
      if (Object.keys(next).length > 0) setSources((prev) => ({ ...prev, ...next }));
    });
    return () => {
      live = false;
    };
  }, [neededIds]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = command.trim();
    if (!trimmed) return;

    setPhase('planning');
    setError(null);
    setResult(null);
    setDecisions({});
    setOrphanDecisions({});
    setCommit(null);

    try {
      const res = await getClient().sendCommand(docId, trimmed);
      setResult(res);
      setPhase('ready');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPhase('failed');
    }
  }

  // Approve/reject and the anchor decisions are local until the user commits.
  // Nothing here talks to the API: the whole set goes in one request, against the
  // base_version the proposal was composed for (ADR-021).
  function decide(changeId: string, approve: boolean) {
    setDecisions((d) => ({ ...d, [changeId]: approve ? 'approved' : 'rejected' }));
  }

  function resolveAnchor(decision: OrphanDecision) {
    setOrphanDecisions((r) => ({ ...r, [decision.anchor_id]: decision }));
  }

  const approvedIds = result
    ? result.changes.map((c) => c.change.change_id).filter((id) => decisions[id] === 'approved')
    : [];
  const rejectedIds = result
    ? result.changes.map((c) => c.change.change_id).filter((id) => decisions[id] === 'rejected')
    : [];

  // Only the orphans belonging to approved changes can block the commit — an
  // orphan under a rejected change is moot, because its transform is not applied.
  const blockingOrphans = orphans.filter(
    ({ changeId, option }) =>
      decisions[changeId] === 'approved' && !orphanDecisions[option.anchor_id],
  );
  const undecidedAnchors = blockingOrphans.length;

  async function commitApproved() {
    if (!result) return;
    setBusy(true);
    setError(null);
    try {
      const res = await getClient().approveChangeSet(result.change_set_id, {
        base_version: result.base_version,
        approved_change_ids: approvedIds,
        rejected_change_ids: rejectedIds,
        orphan_decisions: orphans
          .filter(({ changeId }) => decisions[changeId] === 'approved')
          .map(({ option }) => orphanDecisions[option.anchor_id])
          .filter((d): d is OrphanDecision => Boolean(d)),
      });
      setCommit(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="main" className="relative z-10 content-column py-16">
      <div className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <p className="engraved-label text-muted">Edit console</p>
          <h1 className="mt-2 font-display text-3xl text-primary">Edit by instruction</h1>
        </div>
        <Link
          href={`/documents/${docId}/export`}
          className="rounded border border-cobalt/40 px-5 py-2.5 font-ui text-xs text-cobalt transition-colors duration-ink ease-ink hover:bg-cobalt/[0.06]"
        >
          Export →
        </Link>
      </div>

      {/* Anchors first when any are undecided: an unplaced citation blocks a
          clean export and is the most consequential thing on the screen. */}
      {undecidedAnchors > 0 && (
        <div className="mt-10 rounded border border-madder/40 bg-madder/[0.05] px-5 py-4">
          <p className="inline-flex items-center gap-2 font-ui text-xs text-madder">
            <Seal kind="dangling" size={16} />
            {undecidedAnchors} citation{undecidedAnchors === 1 ? '' : 's'} below need
            {undecidedAnchors === 1 ? 's' : ''} a decision before this edit is complete.
          </p>
        </div>
      )}

      {phase === 'idle' && !result && <EmptyConsole />}

      {phase === 'planning' && (
        <div className="mt-16 flex flex-col items-center py-12 text-center" aria-live="polite">
          <Fleuron size={20} className="text-cobalt/40" />
          <p className="mt-5 font-display text-xl text-primary">Planning your edit</p>
          <p className="measure mt-2 text-xs leading-relaxed text-secondary">
            The planner is turning your instruction into typed operations. Every one of them is
            checked by the invariant kernel before it reaches you.
          </p>
        </div>
      )}

      {phase === 'failed' && (
        <Plate accent="madder" className="mt-12 px-8 py-8">
          <span className="inline-flex items-center gap-3 font-ui text-xs font-medium text-madder">
            <Seal kind="broken" size={18} />
            The command could not be planned
          </span>
          <p className="measure mt-4 text-secondary">
            Your document has not been changed. Try rephrasing the instruction, or narrowing it to
            one section.
          </p>
          {error && (
            <pre className="mt-4 overflow-x-auto whitespace-pre-wrap break-words rounded border border-hair bg-paper-deep px-4 py-3 font-mono text-2xs text-secondary">
              {error}
            </pre>
          )}
        </Plate>
      )}

      {result && (
        <div className="mt-12 space-y-12">
          {/* A change set that reached the user with nothing in it is a real
              answer, not an error — the API said so with a 200 and a reason. */}
          {result.status === 'failed' && (
            <Plate accent="madder" className="px-8 py-8">
              <span className="inline-flex items-center gap-3 font-ui text-xs font-medium text-madder">
                <Seal kind="broken" size={18} />
                No part of this instruction could be applied
              </span>
              <p className="measure mt-4 text-secondary">
                {result.message ??
                  'The planner could not produce a change the kernel would accept.'}
              </p>
              <p className="measure mt-3 text-xs leading-relaxed text-secondary">
                Your document is unchanged at version {result.base_version}. The reasons below are
                the kernel&rsquo;s own.
              </p>
            </Plate>
          )}

          {error && phase === 'ready' && (
            <p
              role="alert"
              className="rounded border border-madder/40 bg-madder/[0.05] px-5 py-4 font-ui text-xs text-madder"
            >
              {error}
            </p>
          )}

          {orphans.length > 0 && (
            <section aria-labelledby="anchors-heading">
              <h2
                id="anchors-heading"
                className="engraved-label text-muted"
              >
                Citations needing a decision
              </h2>
              <ul className="mt-6 space-y-6">
                {orphans.map(({ option }) => (
                  <OrphanedAnchorCard
                    key={option.anchor_id}
                    option={option}
                    sources={sources}
                    resolved={orphanDecisions[option.anchor_id] ?? null}
                    onResolve={resolveAnchor}
                    busy={busy}
                  />
                ))}
              </ul>
            </section>
          )}

          <section aria-labelledby="changes-heading">
            <h2
              id="changes-heading"
              className="engraved-label text-muted"
            >
              Proposed changes
            </h2>
            {result.changes.length === 0 ? (
              <p className="measure mt-4 text-xs leading-relaxed text-secondary">
                The planner produced no valid changes for this instruction. See the refused
                operations below for why.
              </p>
            ) : (
              <ul className="mt-6 space-y-6">
                {result.changes.map((ec) => (
                  <ChangeCard
                    key={ec.change.change_id}
                    evaluated={ec}
                    sources={sources}
                    decision={decisions[ec.change.change_id] ?? 'pending'}
                    onDecide={(approve) => decide(ec.change.change_id, approve)}
                    busy={busy}
                  />
                ))}
              </ul>
            )}

            {result.changes.length > 0 && (
              <CommitBar
                approved={approvedIds.length}
                rejected={rejectedIds.length}
                pending={result.changes.length - approvedIds.length - rejectedIds.length}
                blocked={undecidedAnchors}
                baseVersion={result.base_version}
                busy={busy}
                commit={commit}
                onCommit={commitApproved}
              />
            )}
          </section>

          {result.rejected.length > 0 && (
            <section aria-labelledby="rejected-heading">
              <h2
                id="rejected-heading"
                className="engraved-label text-muted"
              >
                Refused by the kernel
              </h2>
              <ul className="mt-6 space-y-6">
                {result.rejected.map((r, i) => (
                  <RejectedOperationCard key={i} rejected={r} />
                ))}
              </ul>
            </section>
          )}
        </div>
      )}

      {/* Command input, at the bottom — design-system.md §5. */}
      {/*
        Opaque, with a hairline to sit on. At 95% the diff card scrolling under
        this bled through it as a grey smear — a translucent surface over
        running text reads as a rendering fault, not as depth. The composer is
        an instrument at the foot of the desk; it gets a hard edge.
      */}
      <form
        onSubmit={submit}
        className="sticky bottom-0 z-20 mt-16 border-t border-hair bg-paper pb-8 pt-6"
      >
        <div className="h-px w-full bg-[var(--rule-hair)]" />
        <label htmlFor="command" className="mt-6 block font-ui text-2xs uppercase tracking-[0.12em] text-muted">
          Instruction
        </label>
        <div className="mt-3 flex flex-wrap gap-3">
          <textarea
            id="command"
            rows={2}
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                submit(e as unknown as React.FormEvent);
              }
            }}
            placeholder="Tell Answerthat what to change…"
            disabled={phase === 'planning'}
            className="min-w-0 flex-1 resize-y rounded border border-[var(--rule-strong)] bg-leaf px-4 py-3 font-body text-base leading-relaxed text-primary placeholder:text-muted focus:border-cobalt disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={phase === 'planning' || command.trim() === ''}
            className="h-fit self-end rounded border border-cobalt/45 px-6 py-3 font-ui text-xs text-cobalt transition-colors duration-ink ease-ink hover:bg-cobalt/[0.06] disabled:opacity-40"
          >
            {phase === 'planning' ? 'Planning…' : 'Propose changes'}
          </button>
        </div>
        <p className="mt-2 font-ui text-2xs text-muted">
          Nothing is applied until you approve it. ⌘↵ to submit.
        </p>
      </form>
    </main>
  );
}

/**
 * The one write in this screen.
 *
 * Per-change approve/reject is a decision, not a commit: the API applies a change
 * set in a single request against the `base_version` the proposal was composed
 * for (ADR-021). Doing it any other way — a POST per card — is how a half-applied
 * edit becomes possible, and there would be no version to name in the failure.
 */
function CommitBar({
  approved,
  rejected,
  pending,
  blocked,
  baseVersion,
  busy,
  commit,
  onCommit,
}: {
  approved: number;
  rejected: number;
  pending: number;
  blocked: number;
  baseVersion: number;
  busy: boolean;
  commit: CommitResult | null;
  onCommit: () => void;
}) {
  if (commit) {
    const skipped = Object.entries(commit.skipped);
    return (
      <Plate accent={commit.committed ? 'verdigris' : 'madder'} className="mt-8 px-6 py-6 sm:px-8">
        <span
          className={`inline-flex items-center gap-2 font-ui text-xs font-medium ${
            commit.committed ? 'text-verdigris' : 'text-madder'
          }`}
        >
          <Seal kind={commit.committed ? 'filled' : 'broken'} size={17} />
          {commit.committed
            ? `Committed as version ${commit.new_version}`
            : 'Nothing was written'}
        </span>
        <p className="measure mt-3 text-xs leading-relaxed text-secondary">{commit.message}</p>
        {/* A change the user approved that then failed to apply is reported, never
            dropped quietly (HR-3). */}
        {skipped.length > 0 && (
          <ul className="mt-4 space-y-2">
            {skipped.map(([changeId, why]) => (
              <li
                key={changeId}
                className="measure border-l-2 border-madder/40 pl-4 text-xs leading-relaxed text-secondary"
              >
                <span className="font-mono text-2xs text-muted">{changeId}</span> — {why}
              </li>
            ))}
          </ul>
        )}
      </Plate>
    );
  }

  return (
    <div className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-3 border-t border-hair pt-6">
      <button
        type="button"
        disabled={busy || approved === 0 || blocked > 0}
        onClick={onCommit}
        className="rounded border border-verdigris/45 px-6 py-2.5 font-ui text-xs text-verdigris transition-colors duration-ink ease-ink hover:bg-verdigris/[0.07] disabled:opacity-40"
      >
        {busy ? 'Applying…' : `Apply ${approved} approved change${approved === 1 ? '' : 's'}`}
      </button>
      <p className="font-ui text-2xs text-muted">
        {approved} approved · {rejected} rejected · {pending} undecided — committing writes version{' '}
        {baseVersion + 1}.
        {blocked > 0 && (
          <span className="block text-madder">
            {blocked} citation{blocked === 1 ? '' : 's'} above still need
            {blocked === 1 ? 's' : ''} a decision first.
          </span>
        )}
      </p>
    </div>
  );
}

function EmptyConsole() {
  return (
    <div className="mt-16 py-8">
      <p className="measure text-base leading-relaxed text-secondary">
        Describe a change in your own words. Answerthat turns it into typed operations, checks each
        one against the citation invariants, and shows you the diff — nothing is applied until you
        approve it.
      </p>

      <p className="mt-8 font-ui text-2xs uppercase tracking-[0.12em] text-muted">For example</p>
      <ul className="mt-3 space-y-2">
        {EXAMPLES.map((ex) => (
          <li key={ex} className="measure border-l-2 border-[var(--rule-hair)] pl-4 text-base italic text-secondary">
            {ex}
          </li>
        ))}
      </ul>
    </div>
  );
}
