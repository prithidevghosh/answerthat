import { HorizonBand } from '@/components/Ornament';
import { FixtureBanner } from '@/components/FixtureBanner';
import { ConfigurationError } from '@/components/ConfigurationError';
import { WorkbenchHeader } from '@/components/WorkbenchHeader';
import { LoadFailure } from '@/components/LoadFailure';
import { getClient } from '@/lib/api/client';
import { ExportPanel } from './ExportPanel';

export const dynamic = 'force-dynamic';

export default async function ExportPage({ params }: { params: Promise<{ docId: string }> }) {
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

  let manifest;
  let parse;
  try {
    [manifest, parse] = await Promise.all([
      client.getExportManifest(docId),
      client.getParseResult(docId),
    ]);
  } catch (err) {
    return (
      <>
        <FixtureBanner />
        <LoadFailure
          what="the export"
          docId={docId}
          detail={err instanceof Error ? err.message : String(err)}
        />
      </>
    );
  }

  return (
    <>
      <FixtureBanner />
      <WorkbenchHeader
        docId={docId}
        current="export"
        title={parse.document.metadata.title}
        version={parse.document.version}
      />
      <ExportPanel docId={docId} manifest={manifest} />
      {/* The plate returns only at the foot, after the content — never behind it. */}
      <HorizonBand />
    </>
  );
}
