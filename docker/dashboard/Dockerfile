# syntax=docker/dockerfile:1.7
# CELESTRA Cortex dashboard: production image (Next.js standalone server).
#
# Build context: repository root.
#   docker build -f docker/dashboard/Dockerfile -t cortex-dashboard .
#
# Production deploys the dashboard to Vercel; this image serves self-hosted
# and docker compose environments. All configuration is read at runtime, so
# one image serves every environment.

ARG NODE_VERSION=22
ARG PNPM_VERSION=9.15.0
ARG TURBO_VERSION=2.11.4

FROM node:${NODE_VERSION}-alpine AS base
ARG PNPM_VERSION
RUN apk add --no-cache libc6-compat \
    && corepack enable \
    && corepack prepare pnpm@${PNPM_VERSION} --activate
ENV PNPM_HOME=/pnpm \
    NEXT_TELEMETRY_DISABLED=1 \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0
WORKDIR /repo

# ---------------------------------------------------------------------------
# Prune: reduce the monorepo to the dashboard and its workspace dependencies.
# ---------------------------------------------------------------------------
FROM base AS pruner
ARG TURBO_VERSION
RUN npm install --global --no-fund --no-audit turbo@${TURBO_VERSION}
COPY . .
RUN turbo prune @celestra/cortex-dashboard --docker

# ---------------------------------------------------------------------------
# Dependencies: installed from the pruned lockfile only, so source edits keep
# this layer cached.
# ---------------------------------------------------------------------------
FROM base AS deps
COPY --from=pruner /repo/out/json/ .
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir /pnpm/store

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
FROM deps AS builder
COPY --from=pruner /repo/out/full/ .
RUN pnpm --filter @celestra/cortex-dashboard build

# ---------------------------------------------------------------------------
# Runtime: the traced standalone server and static assets only. No pnpm, no
# node_modules beyond what Next traced, non-root.
# ---------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS runner

ARG VERSION=1.0.0-alpha
ARG REVISION=unknown

LABEL org.opencontainers.image.title="cortex-dashboard" \
      org.opencontainers.image.description="CELESTRA Cortex dashboard" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.vendor="CELESTRA"

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    CORTEX_RELEASE=${VERSION}

RUN addgroup --system --gid 10001 cortex \
    && adduser --system --uid 10001 --ingroup cortex cortex

WORKDIR /app

COPY --from=builder --chown=10001:10001 /repo/apps/dashboard/.next/standalone ./
COPY --from=builder --chown=10001:10001 /repo/apps/dashboard/.next/static ./apps/dashboard/.next/static
COPY --from=builder --chown=10001:10001 /repo/apps/dashboard/public ./apps/dashboard/public

USER 10001:10001

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "apps/dashboard/server.js"]
