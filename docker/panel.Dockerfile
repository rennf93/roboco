# RoboCo Control Panel Dockerfile
# Multi-stage build for optimized production image.
# Build context is the project root so the backend and panel share one
# Dockerfile layout under docker/. Paths below are relative to project root.

# =============================================================================
# Base stage
# =============================================================================
FROM node:22-alpine AS base
# Activate the exact version panel/package.json pins (packageManager: pnpm@12.16.1)
# rather than trusting corepack's bundled default — a future node:22-alpine that
# ships a different pnpm would silently change the build's package manager.
# prepare --activate pins the shim to 12.16.1 so corepack and packageManager agree.
# pnpm 12's npm package is a launcher: its first run (the install below)
# downloads the native binary (the musl @pnpm/exe.linux-* package for the
# build's architecture), checked against npm's signature, so only the builder
# stage carries it.
RUN corepack enable pnpm && corepack prepare pnpm@12.16.1 --activate

# =============================================================================
# Build stage
# =============================================================================
FROM base AS builder
WORKDIR /app

# CI=true runs pnpm in CI mode, where `pnpm install` defaults to a frozen
# lockfile. pnpm 11 also needed it to clear a stale node_modules without a TTY
# (ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY); pnpm 12 clears it without asking.
ENV CI=true

# Copy package manifests first (for layer caching). pnpm-workspace.yaml
# carries the `allowBuilds` map (sharp, unrs-resolver) - without it pnpm 12
# fails the install with ERR_PNPM_IGNORED_BUILDS (exit 1) on unrs-resolver's
# postinstall - and `pmOnFail: ignore`, without which a frozen install rejects
# the lockfile for not recording the pinned pnpm.
COPY panel/package.json panel/pnpm-lock.yaml panel/pnpm-workspace.yaml ./

# Install dependencies with the settings `pnpm build` runs with. Before a
# script pnpm checks node_modules against the current settings
# (verifyDepsBeforeRun) and reinstalls when they differ, so an install with
# --shamefully-hoist was recreated unhoisted by `pnpm build` (pnpm 11 and 12
# alike): next build never saw a hoisted tree, and the second install took
# 10 minutes on the NAS with pnpm 11 and 27 with pnpm 12. With the allowBuilds
# approval in pnpm-workspace.yaml, unrs-resolver runs its postinstall, which
# checks its platform-specific binary (sharp 0.35 has no install script) - no
# strictDepBuilds downgrade needed.
RUN pnpm install --frozen-lockfile

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
