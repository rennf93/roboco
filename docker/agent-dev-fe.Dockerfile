# Frontend Developer Agent
# React/TypeScript development

FROM roboco-agent-base

USER root

# Install pnpm globally
RUN npm install -g pnpm

USER agent

LABEL role="frontend-developer"
LABEL description="Frontend developer agent - React, TypeScript"
