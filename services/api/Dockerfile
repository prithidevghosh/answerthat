# Pandoc is a hard dependency, not a nice-to-have: every citation and bibliography in
# this system is rendered by citeproc reading a real .csl file (HR-4). An image without
# it cannot export, cannot detect a style, and cannot run the round-trip check.
#
# Built from the *repo root*, not from services/api — same as apps/web/Dockerfile, and
# for the same reason. packages/csl-styles has to be inside the image: compose can bind
# mount it, but a deployed container has no host to mount from, and an api that cannot
# read a .csl file cannot export at all.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/api

COPY services/api/pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir .

COPY services/api/app ./app
COPY services/api/tests ./tests

# One copy of each style, read by Pandoc here and by citation.js in the browser (HR-4).
# compose still mounts the same directory over this path so a style edit is picked up
# without a rebuild; the copy is what makes the image work anywhere else.
COPY packages/csl-styles /srv/csl-styles
ENV CSL_STYLES_DIR=/srv/csl-styles

EXPOSE 8000

# Shell form, and ${PORT} rather than a literal: platforms that route to a container
# (Railway, Cloud Run) assign the port at run time and a hardcoded 8000 makes every
# request 502 while the process looks perfectly healthy. Compose sets no PORT and gets
# 8000. `exec` so uvicorn is PID 1 and receives SIGTERM directly.
CMD ["sh", "-c", "exec uvicorn --factory app.api.main:asgi --host 0.0.0.0 --port ${PORT:-8000}"]
