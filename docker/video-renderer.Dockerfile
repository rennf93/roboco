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
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libdbus-1-3 \
    libatk1.0-0 \
    libgbm-dev \
    libasound2 \
    libxrandr2 \
    libxkbcommon-dev \
    libxfixes3 \
    libxcomposite1 \
    libxdamage1 \
    libatk-bridge2.0-0 \
    libpango-1.0-0 \
    libcairo2 \
    libcups2 \
    ffmpeg \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Activate the exact version video-renderer/package.json pins
# (packageManager: pnpm@11.10.0) rather than trusting corepack's bundled
# default — a future node:22-alpine shipping a different pnpm would silently
# change the build's package manager.
RUN corepack enable pnpm && corepack prepare pnpm@11.10.0 --activate

WORKDIR /app

# pnpm 11 prompts for confirmation on modules-purge unless told this is CI
# (same gotcha as docker/panel.Dockerfile).
ENV CI=true

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
# real render isn't also the first time it downloads a browser.
COPY video-renderer/ensure-browser.mjs ./
RUN node ensure-browser.mjs

COPY video-renderer/server.js video-renderer/render.js ./

EXPOSE 3001
ENV PORT=3001

CMD ["node", "server.js"]
