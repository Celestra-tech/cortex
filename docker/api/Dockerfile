# syntax=docker/dockerfile:1.7
# CELESTRA Cortex API: production image.
#
# Build context: repository root.
#   docker build -f docker/api/Dockerfile -t cortex-api .
#
# Commands (see entrypoint.sh):
#   serve     (default) uvicorn on $PORT
#   migrate   alembic upgrade head, then exit; run as a one-off job before deploys
#   cli ...   python -m cortex_api.cli ..., e.g. `cli create-organization --name Acme --slug acme`

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.12

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------------------
# Builder: resolve the locked dependency set into /opt/venv.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /workspace

# Every workspace member's manifest is needed to read the lockfile, but only
# cortex-api's dependencies are installed. Manifests first, so this layer is
# cached until dependencies change.
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/pyproject.toml apps/api/README.md apps/api/
COPY packages/sdk-py/pyproject.toml packages/sdk-py/README.md packages/sdk-py/
COPY services/auth/pyproject.toml services/auth/README.md services/auth/
COPY services/knowledge/pyproject.toml services/knowledge/README.md services/knowledge/
COPY services/memory/pyproject.toml services/memory/README.md services/memory/
COPY services/observatory/pyproject.toml services/observatory/README.md services/observatory/
COPY services/router/pyproject.toml services/router/README.md services/router/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package cortex-api --no-install-workspace

COPY apps/api/src apps/api/src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package cortex-api --no-editable \
    && find /opt/venv -type d -name tests -prune -exec rm -rf {} +

# ---------------------------------------------------------------------------
# Runtime: slim Python, the virtualenv, and migration assets. No compilers,
# no package manager caches, non-root.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ARG VERSION=1.0.0-alpha
ARG REVISION=unknown

LABEL org.opencontainers.image.title="cortex-api" \
      org.opencontainers.image.description="CELESTRA Cortex API" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.vendor="CELESTRA"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PORT=8000 \
    CORTEX_ENV=production \
    CORTEX_LOG_FORMAT=json \
    CORTEX_RELEASE=${VERSION} \
    CORTEX_REVISION=${REVISION}

RUN groupadd --system --gid 10001 cortex \
    && useradd --system --uid 10001 --gid cortex --no-create-home --shell /usr/sbin/nologin cortex

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY apps/api/alembic.ini ./alembic.ini
COPY apps/api/migrations ./migrations
COPY --chmod=0755 docker/api/entrypoint.sh /usr/local/bin/cortex-entrypoint

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/health\", timeout=4).status == 200 else 1)"]

ENTRYPOINT ["cortex-entrypoint"]
CMD ["serve"]
