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
  let document;
  try {
    /*
      The document, not the parse report. Export is about the current head — its title
      and its version — and the parse report is a record of one ingestion run, which is
      held in process and does not survive an API restart. Reading it here meant a
      restart turned a perfectly exportable document into "could not load the export",
      naming a failed call the user has no way to connect to their paper.
    */
    [manifest, document] = await Promise.all([
      client.getExportManifest(docId),
      client.getDocument(docId),
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
        title={document.metadata.title}
        version={document.version}
      />
      <ExportPanel docId={docId} manifest={manifest} />
      {/* The plate returns only at the foot, after the content — never behind it. */}
      <HorizonBand />
    </>
  );
}
