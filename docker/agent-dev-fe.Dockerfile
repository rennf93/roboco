# Frontend Developer Agent
# React/TypeScript development with browser automation

FROM roboco-agent-base

USER root

# Playwright system dependencies
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

# Install pnpm globally
RUN npm install -g pnpm

USER agent

# Install Playwright (browsers will be installed on first run or can be cached)
RUN npx playwright install chromium

LABEL role="frontend-developer"
LABEL description="Frontend developer agent - React, TypeScript, Playwright"
