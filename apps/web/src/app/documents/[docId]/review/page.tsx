import { MarginPlate, MarginFoot } from '@/components/Ornament';
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
      {/* Pl. II. */}
      <MarginPlate plate={2} />
      <div className="leaf">
      <WorkbenchHeader
        docId={docId}
        current="review"
        title={parse.document.metadata.title}
        version={parse.document.version}
      />
      {/*
        The document's style, then the detector's, then a last resort.

        `parse.style.style_id` is the detector's raw verdict, which is null whenever two
        candidates tied — so this read fell through to the hardcoded 'ieee' and rendered
        every finding's citation as a bracketed number for a paper written in author-date.
        The resolved style lives on the document: detection's closest match is persisted
        there at ingest, and a user's explicit choice overwrites it.
      */}
      <ReviewFeed
        docId={docId}
        styleId={
          parse.document.metadata.style_id ?? parse.style?.style_id ?? 'chicago-author-date'
        }
      />
        <MarginFoot plate={2} />
      </div>
    </>
  );
}
