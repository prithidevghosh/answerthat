import { MarginPlates } from '@/components/Ornament';
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

  let parse;
  try {
    parse = await client.getParseResult(docId);
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
      <MarginPlates strength="quiet" />
      <WorkbenchHeader
        docId={docId}
        current="edit"
        title={parse.document.metadata.title}
        version={parse.document.version}
      />
      <EditConsole docId={docId} />
    </>
  );
}
