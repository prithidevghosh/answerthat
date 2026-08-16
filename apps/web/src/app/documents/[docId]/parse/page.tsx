import { MarginPlate, MarginFoot } from '@/components/Ornament';
import { FixtureBanner } from '@/components/FixtureBanner';
import { ConfigurationError } from '@/components/ConfigurationError';
import { WorkbenchHeader } from '@/components/WorkbenchHeader';
import { LoadFailure } from '@/components/LoadFailure';
import { getClient } from '@/lib/api/client';
import type { SourceRecord } from '@/lib/contracts';
import { ParseInspector } from './ParseInspector';
import { ParsePending } from './ParsePending';

export const dynamic = 'force-dynamic';

export default async function ParsePage({ params }: { params: Promise<{ docId: string }> }) {
  const { docId } = await params;
  const client = getClient();

  const status = await client.getStatus();
  if (status.kind !== 'ok') {
    return (
      <>
        <FixtureBanner />
        <ConfigurationError status={status} />
      </>
    );
  }

  let result;
  try {
    result = await client.getParseResult(docId);
  } catch (err) {
    // `/parse` 404s until the IR is written, so a failure here is not yet
    // evidence of a failure. Ask the ingest what is actually going on before
    // telling the user their paper could not be loaded: "still parsing",
    // "the parse failed, here is why", and "we asked and got nothing usable"
    // are three different things and were all being shown as the third.
    const status = await client.getParseStatus(docId).catch(() => null);

    if (status && (status.state === 'queued' || status.state === 'running')) {
      return (
        <>
          <FixtureBanner />
          <ParsePending docId={docId} initial={status} />
        </>
      );
    }

    return (
      <>
        <FixtureBanner />
        <LoadFailure
          what="parse results"
          docId={docId}
          // The parse's own reason when it has one. It is far more useful than
          // the 404 that brought us here, which only says the IR is absent.
          detail={
            status?.error ?? (err instanceof Error ? err.message : String(err))
          }
        />
      </>
    );
  }

  // Hydrate the source records behind resolved references so each card can link
  // to a real external URL. A record we cannot fetch simply has no link — the
  // card still renders, and never invents one.
  const ids = [...new Set(result.references.map((r) => r.source_id).filter((x): x is string => !!x))];
  const settled = await Promise.allSettled(ids.map((id) => client.getSource(id)));
  const sources: Record<string, SourceRecord> = {};
  settled.forEach((s) => {
    if (s.status === 'fulfilled') sources[s.value.source_id] = s.value;
  });

  return (
    <>
      <FixtureBanner />
      {/* Pl. I. The plate is held in the outer margin and the text block sits in
          the open field beside it — see the note on `.leaf` in globals.css. */}
      <MarginPlate plate={1} />
      <div className="leaf">
        <WorkbenchHeader
          docId={docId}
          current="parse"
          title={result.document.metadata.title}
          version={result.document.version}
        />
        <ParseInspector docId={docId} result={result} sources={sources} />
        <MarginFoot plate={1} />
      </div>
    </>
  );
}
