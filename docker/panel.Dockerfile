# RoboCo Control Panel Dockerfile
# Multi-stage build for optimized production image.
# Build context is the project root so the backend and panel share one
# Dockerfile layout under docker/. Paths below are relative to project root.

# =============================================================================
# Base stage
# =============================================================================
FROM node:22-alpine AS base
# Activate the exact version panel/package.json pins (packageManager: pnpm@11.10.0)
# rather than trusting corepack's bundled default — a future node:22-alpine that
# ships a different pnpm would silently change the build's package manager.
# prepare --activate pins the shim to 11.10.0 so corepack and packageManager agree.
RUN corepack enable pnpm && corepack prepare pnpm@11.10.0 --activate

# =============================================================================
# Build stage
# =============================================================================
FROM base AS builder
WORKDIR /app

# pnpm 11 prompts for confirmation on modules-purge unless told this is CI.
ENV CI=true

# Copy package manifests first (for layer caching). pnpm-workspace.yaml
# carries the `allowBuilds` map (sharp, unrs-resolver) — without it pnpm 11
# hard-errors with [ERR_PNPM_IGNORED_BUILDS] (exit 1) on sharp's postinstall.
COPY panel/package.json panel/pnpm-lock.yaml panel/pnpm-workspace.yaml ./

# Install dependencies with shamefully-hoist to flatten node_modules
# (prevents symlink issues with styled-jsx and other peer deps). With the
# allowBuilds approval in pnpm-workspace.yaml, sharp and unrs-resolver run
# their postinstall scripts and install their platform-specific binaries —
# no strictDepBuilds downgrade needed.
RUN pnpm install \
    --frozen-lockfile \
    --shamefully-hoist

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

# Create non-root user. -G makes nodejs its primary group (busybox adduser
# otherwise gives a system user `nogroup`), matching the chowns below.
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 -G nodejs nextjs

# Copy public assets. Must be chowned to the runtime user (nextjs) like the
# standalone/static copies below — without it the assets stay root:root
# rwxrwx--- and the non-root nextjs process gets EACCES serving them, so every
# /public file (e.g. roboco-logo.png) 500s.
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

# Copy standalone build
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Numeric (nextjs:nodejs), so a runtime that cannot read this image's
# /etc/passwd (or that must prove the user is not root) still resolves it.
USER 1001:1001

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

# /login is the one page that renders without the orchestrator: proxy.ts
# excludes it from its matcher (every other route first probes the backend's
# /auth/status), and it is prerendered. A 200 means the Next server is up and
# serving this build, whatever the backend is doing.
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then((r) => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

CMD ["node", "server.js"]
