# RoboCo Control Panel Dockerfile
# Multi-stage build for optimized production image.
# Build context is the project root so the backend and panel share one
# Dockerfile layout under docker/. Paths below are relative to project root.

# =============================================================================
# Base stage
# =============================================================================
FROM node:22-alpine AS base
RUN corepack enable pnpm

# =============================================================================
# Build stage
# =============================================================================
FROM base AS builder
WORKDIR /app

# pnpm 11 prompts for confirmation on modules-purge unless told this is CI.
ENV CI=true

# Copy package manifests first (for layer caching)
COPY panel/package.json panel/pnpm-lock.yaml ./

# Install dependencies with shamefully-hoist to flatten node_modules
# (prevents symlink issues with styled-jsx and other peer deps).
#
# pnpm 11 hard-errors on packages with install scripts unless explicitly
# approved. `sharp` and `unrs-resolver` both ship platform-specific
# prebuilt binaries via @img/sharp-* and napi-postinstall, so the install
# scripts are verification-only — skipping them is safe at runtime.
# `strictDepBuilds=false` downgrades the hard error to a warning while
# keeping the install reproducible against the frozen lockfile.
RUN pnpm install \
    --frozen-lockfile \
    --shamefully-hoist \
    --config.strictDepBuilds=false

# Copy panel source code
COPY panel/ ./

# Build the application
# NOTE: No NEXT_PUBLIC_* env vars set here - we use relative URLs
# which nginx proxies to the orchestrator
RUN pnpm build

# =============================================================================
# Production stage
# =============================================================================
FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production

# Create non-root user
RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# Copy public assets
COPY --from=builder /app/public ./public

# Copy standalone build
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
