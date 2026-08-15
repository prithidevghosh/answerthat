import { HorizonBand } from '@/components/Ornament';
import { FixtureBanner } from '@/components/FixtureBanner';
import { ConfigurationError } from '@/components/ConfigurationError';
import { WorkbenchHeader } from '@/components/WorkbenchHeader';
import { LoadFailure } from '@/components/LoadFailure';
import { getClient } from '@/lib/api/client';
import type { SourceRecord } from '@/lib/contracts';
import { ParseInspector } from './ParseInspector';

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
    return (
      <>
        <FixtureBanner />
        <LoadFailure
          what="parse results"
          docId={docId}
          detail={err instanceof Error ? err.message : String(err)}
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
      <WorkbenchHeader
        docId={docId}
        current="parse"
        title={result.document.metadata.title}
        version={result.document.version}
      />
      <ParseInspector docId={docId} result={result} sources={sources} />
      {/* The plate returns only at the foot, after the content — never behind it. */}
      <HorizonBand />
    </>
  );
}
