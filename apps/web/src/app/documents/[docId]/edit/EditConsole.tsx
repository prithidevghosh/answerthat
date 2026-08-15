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
import type { AnchorResolution, CommandResult } from '@/lib/api/types';
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
  const [anchorResolutions, setAnchorResolutions] = useState<Record<string, AnchorResolution>>({});
  const [sources, setSources] = useState<Record<string, SourceRecord>>({});
  const [busy, setBusy] = useState(false);

  const neededIds = useMemo(() => {
    if (!result) return [];
    const ids = new Set<string>();
    result.changes.forEach((c) => c.change.new_source_ids.forEach((s) => ids.add(s)));
    result.orphaned_anchors.forEach((a) => a.source_ids.forEach((s) => ids.add(s)));
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

  const anchorSources = useMemo(() => {
    const map: Record<string, string[]> = {};
    result?.orphaned_anchors.forEach((a) => {
      map[a.anchor_id] = a.source_ids;
    });
    return map;
  }, [result]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = command.trim();
    if (!trimmed) return;

    setPhase('planning');
    setError(null);
    setResult(null);
    setDecisions({});
    setAnchorResolutions({});

    try {
      const res = await getClient().sendCommand(docId, trimmed);
      setResult(res);
      setPhase('ready');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPhase('failed');
    }
  }

  async function decide(changeId: string, approve: boolean) {
    setBusy(true);
    try {
      await getClient().decideChange(docId, changeId, approve);
      setDecisions((d) => ({ ...d, [changeId]: approve ? 'approved' : 'rejected' }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function resolveAnchor(anchorId: string, res: AnchorResolution) {
    setBusy(true);
    try {
      await getClient().resolveAnchor(docId, anchorId, res);
      setAnchorResolutions((r) => ({ ...r, [anchorId]: res }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const undecidedAnchors =
    result?.orphaned_anchors.filter((a) => !anchorResolutions[a.anchor_id]).length ?? 0;

  return (
    <main id="main" className="relative z-10 content-column py-16">
      <div className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <p className="font-ui text-2xs uppercase tracking-[0.14em] text-muted">Edit console</p>
          <h1 className="mt-2 font-display text-3xl text-primary">Edit by instruction</h1>
        </div>
        <Link
          href={`/documents/${docId}/export`}
          className="rounded border border-indigo/40 px-5 py-2.5 font-ui text-xs text-indigo transition-colors duration-ink ease-ink hover:bg-indigo/[0.06]"
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
          <Fleuron size={20} className="text-indigo/40" />
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
          {result.orphaned_anchors.length > 0 && (
            <section aria-labelledby="anchors-heading">
              <h2
                id="anchors-heading"
                className="font-ui text-2xs uppercase tracking-[0.14em] text-muted"
              >
                Citations needing a decision
              </h2>
              <ul className="mt-6 space-y-6">
                {result.orphaned_anchors.map((a) => (
                  <OrphanedAnchorCard
                    key={a.anchor_id}
                    decision={a}
                    sources={sources}
                    resolved={anchorResolutions[a.anchor_id] ?? null}
                    onResolve={(res) => resolveAnchor(a.anchor_id, res)}
                    busy={busy}
                  />
                ))}
              </ul>
            </section>
          )}

          <section aria-labelledby="changes-heading">
            <h2
              id="changes-heading"
              className="font-ui text-2xs uppercase tracking-[0.14em] text-muted"
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
                {result.changes.map((rc) => (
                  <ChangeCard
                    key={rc.change.change_id}
                    reviewed={rc}
                    sources={sources}
                    anchorSources={anchorSources}
                    decision={decisions[rc.change.change_id] ?? 'pending'}
                    onDecide={(approve) => decide(rc.change.change_id, approve)}
                    busy={busy}
                  />
                ))}
              </ul>
            )}
          </section>

          {result.rejected.length > 0 && (
            <section aria-labelledby="rejected-heading">
              <h2
                id="rejected-heading"
                className="font-ui text-2xs uppercase tracking-[0.14em] text-muted"
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
      <form onSubmit={submit} className="sticky bottom-0 z-20 mt-16 bg-paper/95 pb-8 pt-6 backdrop-blur-[2px]">
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
            className="min-w-0 flex-1 resize-y rounded border border-[var(--rule-strong)] bg-plate px-4 py-3 font-body text-base leading-relaxed text-primary placeholder:text-muted focus:border-indigo disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={phase === 'planning' || command.trim() === ''}
            className="h-fit self-end rounded border border-indigo/45 px-6 py-3 font-ui text-xs text-indigo transition-colors duration-ink ease-ink hover:bg-indigo/[0.06] disabled:opacity-40"
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
