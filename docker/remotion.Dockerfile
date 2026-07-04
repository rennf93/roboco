# remotion-renderer sidecar — untars a motion/ composition source, bundles
# it, renders one MP4 cut with Remotion, and streams the bytes back.
#
# Debian (not Alpine): musl slows renders past 10s and has known Chrome
# Headless Shell compatibility issues (verified against Remotion's own
# Docker guidance). FFmpeg is bundled inside @remotion/renderer since v4 —
# no system ffmpeg package here.
#
# Build context is the project root, like every other service under
# docker/ — paths below are relative to the repo root.
FROM node:22-bookworm-slim

# Chrome/Puppeteer runtime libraries — Remotion's documented Debian
# dependency list for headless rendering. A missing one surfaces as an
# opaque Puppeteer "Target closed" crash, not a clear missing-library error.
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
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

RUN corepack enable pnpm

WORKDIR /app

# pnpm 11 prompts for confirmation on modules-purge unless told this is CI
# (same gotcha as docker/panel.Dockerfile).
ENV CI=true

# Install dependencies first (layer caching). Unlike the panel image, every
# dependency here runs at request time — bundle()/renderMedia() execute
# live per /render call — so there's no separate build output to discard
# and no multi-stage split.
COPY remotion-renderer/package.json remotion-renderer/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Pre-warm Chrome Headless Shell at build time so the container's first
# real render isn't also the first time it downloads a browser.
COPY remotion-renderer/ensure-browser.mjs ./
RUN node ensure-browser.mjs

COPY remotion-renderer/server.js remotion-renderer/render.js ./

EXPOSE 3001
ENV PORT=3001

CMD ["node", "server.js"]
