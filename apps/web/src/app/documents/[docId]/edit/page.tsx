import { MarginPlate, MarginFoot } from '@/components/Ornament';
import { FixtureBanner } from '@/components/FixtureBanner';
import { ConfigurationError } from '@/components/ConfigurationError';
import { WorkbenchHeader } from '@/components/WorkbenchHeader';
import { LoadFailure } from '@/components/LoadFailure';
import { getClient } from '@/lib/api/client';
import { EditConsole } from './EditConsole';

export const dynamic = 'force-dynamic';

export default async function EditPage({ params }: { params: Promise<{ docId: string }> }) {
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

  // The document, not the parse report: this screen needs a title and a version,
  // and the parse report is an in-process record of one ingest that does not
  // survive an API restart. Hanging the console off it made a perfectly editable
  // document report itself as unloadable.
  let document;
  try {
    document = await client.getDocument(docId);
  } catch (err) {
    return (
      <>
        <FixtureBanner />
        <LoadFailure
          what="this document"
          docId={docId}
          detail={err instanceof Error ? err.message : String(err)}
        />
      </>
    );
  }

  return (
    <>
      <FixtureBanner />
      {/* Pl. III. */}
      <MarginPlate plate={3} />
      <div className="leaf">
        <WorkbenchHeader
          docId={docId}
          current="edit"
          title={document.metadata.title}
          version={document.version}
        />
        <EditConsole docId={docId} />
        <MarginFoot plate={3} />
      </div>
    </>
  );
}
