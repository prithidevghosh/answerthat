import { FixtureBanner } from '@/components/FixtureBanner';
import { ConfigurationError } from '@/components/ConfigurationError';
import { LoadFailure } from '@/components/LoadFailure';
import { getClient } from '@/lib/api/client';
import type { DocumentIR } from '@/lib/contracts';
import { ChatConsole } from './ChatConsole';

// Per request, like every other screen: a server that started before the keys
// were fixed must not keep serving a cached configuration error, or the reverse.
export const dynamic = 'force-dynamic';

/**
 * The conversational flow.
 *
 * The HR-2 probe matters more here than anywhere else, because this is the first
 * screen a user lands on in this flow — the fork navigates straight here on the
 * 202 — so it is where a missing key gets discovered. Without the probe the
 * failure would surface as an agent that never says anything.
 *
 * Everything else on this page is deliberately tolerant of an unfinished parse.
 * `getDocument` 404s until the IR is written, and arriving before that is the
 * *normal* case here rather than an error: the agent's first job is to narrate
 * the ingest. So a missing document is only a failure once `parse-status` says
 * there is no ingest either.
 */
export default async function ChatPage({ params }: { params: Promise<{ docId: string }> }) {
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

  let document: DocumentIR | null = null;
  try {
    document = await client.getDocument(docId);
  } catch {
    document = null;
  }

  if (!document) {
    const parse = await client.getParseStatus(docId).catch(() => null);

    // No document and no ingest: there is genuinely no such paper, and starting
    // a conversation about it would be a conversation about nothing.
    if (!parse) {
      return (
        <>
          <FixtureBanner />
          <LoadFailure
            what="this document"
            docId={docId}
            detail={`The API knows of no document ${docId} and no ingest for it.`}
          />
        </>
      );
    }

    if (parse.state === 'failed') {
      return (
        <>
          <FixtureBanner />
          {/* The backend's own reason, verbatim. Never "something went wrong". */}
          <LoadFailure what="this document" docId={docId} detail={parse.error} />
        </>
      );
    }
  }

  /**
   * The style used to render citations inside the transcript.
   *
   * The document's resolved style first — detection's closest match is persisted
   * there at ingest and a user's explicit choice overwrites it. While the ingest
   * is still running there is no document, and no style either; the fallback is
   * the same one the review feed uses rather than a guess that renders an
   * author-date paper in brackets.
   */
  const styleId = document?.metadata.style_id ?? 'chicago-author-date';

  return (
    <>
      <FixtureBanner />
      <ChatConsole
        docId={docId}
        styleId={styleId}
        title={document?.metadata.title ?? null}
        version={document?.version ?? null}
      />
    </>
  );
}
