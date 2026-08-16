# Built from the *repo root* (see docker-compose.yml's `context: .` for this
# service), not from apps/web, because scripts/sync-csl-styles.mjs resolves
# packages/csl-styles via a relative path (../../../packages/csl-styles) and
# needs that directory to exist at the same depth inside the image (HR-4: web
# and api must read byte-identical .csl files).
FROM node:22-alpine

RUN corepack enable

WORKDIR /repo
COPY packages/csl-styles ./packages/csl-styles
COPY apps/web ./apps/web

WORKDIR /repo/apps/web
RUN pnpm install --frozen-lockfile

# NEXT_PUBLIC_* vars are inlined into the client bundle at build time, so this
# has to be an ARG/ENV available to `pnpm build`, not just a runtime env var —
# docker-compose.yml passes it through via build.args.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN pnpm build

EXPOSE 3000

CMD ["pnpm", "start"]
