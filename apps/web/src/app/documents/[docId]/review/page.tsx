import { MarginPlates } from '@/components/Ornament';
import { FixtureBanner } from '@/components/FixtureBanner';
import { ConfigurationError } from '@/components/ConfigurationError';
import { WorkbenchHeader } from '@/components/WorkbenchHeader';
import { LoadFailure } from '@/components/LoadFailure';
import { getClient } from '@/lib/api/client';
import { ReviewFeed } from './ReviewFeed';

export const dynamic = 'force-dynamic';

export default async function ReviewPage({ params }: { params: Promise<{ docId: string }> }) {
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

  // The style is needed to render each finding's source citation through
  // citeproc, so it is fetched here rather than guessed in the client.
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
        current="review"
        title={parse.document.metadata.title}
        version={parse.document.version}
      />
      <ReviewFeed docId={docId} styleId={parse.style.style_id ?? 'ieee'} />
    </>
  );
}
