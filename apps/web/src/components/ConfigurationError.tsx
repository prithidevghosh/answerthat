import { Plate } from './Plate';
import { RuleWithFleuron } from './Ornament';
import { Seal } from './Seal';
import type { ApiStatus } from '@/lib/api/types';
import { API_BASE } from '@/lib/api/client';

const KEY_HELP: Record<string, { what: string; where: string; url: string }> = {
  SEMANTIC_SCHOLAR_API_KEY: {
    what: 'Semantic Scholar — paper search, matching, recommendations and abstracts.',
    where: 'semanticscholar.org/product/api',
    url: 'https://www.semanticscholar.org/product/api',
  },
  OPENALEX_API_KEY: {
    what: 'OpenAlex — the second index, and the one-hop citation graph expansion.',
    where: 'openalex.org',
    url: 'https://openalex.org/',
  },
  OPENALEX_MAILTO: {
    what: 'Your contact address, required for the OpenAlex polite pool.',
    where: 'any address you can receive mail at',
    url: 'https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication',
  },
  ANTHROPIC_API_KEY: {
    what: 'Claim extraction, the constrained repair tier, verification and the planner.',
    where: 'console.anthropic.com',
    url: 'https://console.anthropic.com/',
  },
};

/**
 * HR-2, as a screen.
 *
 * The API refuses to start without both provider keys, and that is deliberate:
 * under anonymous limits searches do not error, they return thin results that
 * the review pipeline would report as "no missing work found" — a false
 * negative dressed as a clean bill of health (ADR-010).
 *
 * So this is a calm configuration notice, not a crash page. It explains what is
 * missing, why the refusal is the correct behaviour, and exactly how to fix it.
 */
export function ConfigurationError({ status }: { status: ApiStatus }) {
  const unreachable = status.kind === 'unreachable';
  const keys = status.missing_keys.length > 0 ? status.missing_keys : Object.keys(KEY_HELP).slice(0, 2);

  return (
    <main id="main" className="content-column py-24">
      <Plate accent="madder" className="px-8 py-12 sm:px-12">
        <div className="measure mx-auto">
          <span className="inline-flex items-center gap-3 font-ui text-xs font-medium text-madder">
            <Seal kind="broken" size={20} />
            {unreachable ? 'The API is not running' : 'The API is not configured'}
          </span>

          <h1 className="mt-6 text-3xl">
            {unreachable
              ? 'Answerthat cannot reach its API.'
              : 'Answerthat will not start without its API keys.'}
          </h1>

          <p className="mt-6 text-secondary">
            {unreachable ? (
              <>
                Nothing is wrong with your document. The web app is running, but the API at{' '}
                <code className="text-xs text-primary">{API_BASE}</code>{' '}
                did not answer. The most common cause is that it refused to start because a required
                key is missing — which is intended behaviour, not a bug.
              </>
            ) : (
              <>
                Both provider keys are required, and the application raises on startup if either is
                absent. There is no anonymous mode and no degraded fallback.
              </>
            )}
          </p>

          <RuleWithFleuron className="my-12" />

          <h2 className="text-lg">
            {unreachable ? 'What to check' : 'What is missing'}
          </h2>

          <ul className="mt-6 space-y-6">
            {keys.map((key) => {
              const help = KEY_HELP[key];
              return (
                <li key={key} className="border-l-2 border-madder/40 pl-6">
                  <code className="font-mono text-xs font-medium text-primary">{key}</code>
                  {help && (
                    <>
                      <p className="mt-2 text-xs text-secondary">{help.what}</p>
                      <p className="mt-1 font-ui text-2xs text-muted">
                        Free key from{' '}
                        <a
                          href={help.url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-indigo underline decoration-indigo/30 underline-offset-2 hover:decoration-indigo"
                        >
                          {help.where}
                        </a>
                      </p>
                    </>
                  )}
                </li>
              );
            })}
          </ul>

          <div className="mt-12 rounded border border-hair bg-paper-deep px-6 py-6">
            <p className="font-ui text-2xs uppercase tracking-[0.12em] text-muted">
              To fix
            </p>
            <pre className="mt-3 overflow-x-auto font-mono text-xs leading-relaxed text-primary">
              <code>{`cp .env.example .env
# fill in the keys above, then
docker compose up -d`}</code>
            </pre>
          </div>

          {status.detail && (
            <p className="mt-8 font-mono text-2xs leading-relaxed text-muted">
              Reported: {status.detail}
            </p>
          )}

          <RuleWithFleuron className="my-12" />

          <p className="text-xs leading-relaxed text-secondary">
            <strong className="font-normal text-primary">Why this is a hard stop.</strong> Without a
            key, provider searches do not fail loudly — they come back thin or empty. A review built
            on those results would tell you it found no missing work, and you would have no way to
            tell that from a paper with genuinely complete citations. Refusing to start is the only
            honest option.
          </p>
        </div>
      </Plate>
    </main>
  );
}
