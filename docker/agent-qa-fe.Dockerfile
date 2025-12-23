# Frontend QA Agent
# Browser testing and accessibility tools

FROM roboco-agent-base

USER root

# Playwright system dependencies (same as fe-dev)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g pnpm

USER agent

# Playwright for browser testing
RUN npx playwright install chromium

LABEL role="frontend-qa"
LABEL description="Frontend QA agent - browser testing, accessibility, visual regression"
