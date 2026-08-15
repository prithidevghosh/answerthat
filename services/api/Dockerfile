# Pandoc is a hard dependency, not a nice-to-have: every citation and bibliography in
# this system is rendered by citeproc reading a real .csl file (HR-4). An image without
# it cannot export, cannot detect a style, and cannot run the round-trip check.
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

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir .

COPY app ./app
COPY tests ./tests

# packages/csl-styles/ is mounted at runtime (see docker-compose.yml) rather than copied,
# so the api and the frontend read byte-identical style files. Two copies is how preview
# and export drift apart.
ENV CSL_STYLES_DIR=/srv/csl-styles

EXPOSE 8000

CMD ["uvicorn", "--factory", "app.api.main:asgi", "--host", "0.0.0.0", "--port", "8000"]
