# video-renderer sidecar — untars a motion/ composition source, renders one
# MP4 cut with HyperFrames, and streams the bytes back.
#
# Debian (not Alpine): musl slows renders past 10s and has known Chrome
# Headless Shell compatibility issues (verified against upstream
# headless-Chrome Docker guidance). HyperFrames' @hyperframes/producer
# encodes via a system ffmpeg binary — unlike @remotion/renderer (which
# bundled ffmpeg), the producer shells out to ffmpeg on PATH, so install
# it here.
#
# Build context is the project root, like every other service under
# docker/ — paths below are relative to the repo root.
FROM node:22-bookworm-slim

# Chrome/Puppeteer runtime libraries — the documented Debian dependency
# list for headless rendering (per Puppeteer/Chrome guidance). A missing
# one surfaces as an opaque Puppeteer "Target closed" crash, not a clear
# missing-library error.
# ffmpeg: required by @hyperframes/producer's local render mode — see top
# comment; without it renders fail with "FFmpeg not found".
# Pins follow agent-base.Dockerfile's scheme: the upstream series bookworm
# (fixed by the tag) ships for its whole life, never a Debian revision, and
# ca-certificates by suite (bookworm-security), never by version.
RUN apt-get update && apt-get install -y --no-install-recommends \
    'libnss3=2:3.87.*' \
    'libdbus-1-3=1.14.*' \
    'libatk1.0-0=2.46.*' \
    'libgbm-dev=22.3.*' \
    'libasound2=1.2.8*' \
    'libxrandr2=2:1.5.*' \
    'libxkbcommon-dev=1.5.*' \
    'libxfixes3=1:6.0.*' \
    'libxcomposite1=1:0.4.*' \
    'libxdamage1=1:1.1.*' \
    'libatk-bridge2.0-0=2.46.*' \
    'libpango-1.0-0=1.50.*' \
    'libcairo2=1.16.*' \
    'libcups2=2.4.*' \
    'ffmpeg=7:5.1.*' \
    ca-certificates/bookworm-security \
  && rm -rf /var/lib/apt/lists/*

# Activate the exact version video-renderer/package.json pins
# (packageManager: pnpm@11.10.0) rather than trusting corepack's bundled
# default — a future node:22-alpine shipping a different pnpm would silently
# change the build's package manager.
RUN corepack enable pnpm && corepack prepare pnpm@11.10.0 --activate

WORKDIR /app

# pnpm 11 prompts for confirmation on modules-purge unless told this is CI
# (same gotcha as docker/panel.Dockerfile).
# PUPPETEER_CACHE_DIR: the server runs as `node` (below), and
# @hyperframes/engine looks for chrome-headless-shell only under the running
# user's ~/.cache/puppeteer, so puppeteer's postinstall downloads it there.
ENV CI=true \
    PUPPETEER_CACHE_DIR=/home/node/.cache/puppeteer

# Install dependencies first (layer caching). Unlike the panel image, every
# dependency here runs at request time — createRenderJob()/executeRenderJob()
# execute live per /render call — so there's no separate build output to discard
# and no multi-stage split.
# pnpm-workspace.yaml carries the `allowBuilds: esbuild: true` approval —
# without it pnpm 11 hard-errors with [ERR_PNPM_IGNORED_BUILDS] (exit 1)
# because esbuild's postinstall is unapproved, breaking the image build.
COPY video-renderer/package.json video-renderer/pnpm-lock.yaml video-renderer/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

# Pre-warm Chrome Headless Shell at build time so the container's first
# real render isn't also the first time it downloads a browser. The
# browser, node_modules and server stay root-owned (the server cannot
# rewrite them); renders write only to os.tmpdir().
COPY video-renderer/ensure-browser.mjs ./

# The base image's `node` user, numeric. Chrome runs with --no-sandbox
# (@hyperframes/engine's launch flags) either way; this takes root away from
# the agent-authored composition HTML/JS it executes. ensure-browser.mjs runs
# after the switch so it checks the browser exactly where a render looks.
USER 1000:1000
RUN node ensure-browser.mjs

COPY video-renderer/server.js video-renderer/render.js ./

EXPOSE 3001
ENV PORT=3001

# Same probe compose pins (docker-compose*.yml video-renderer healthcheck).
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3001/health').then((r) => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

CMD ["node", "server.js"]
