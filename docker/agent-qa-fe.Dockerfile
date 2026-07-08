# Frontend QA Agent
# Accessibility and code-level testing tools

FROM roboco-agent-base

USER root

RUN npm install -g pnpm

USER agent

LABEL role="frontend-qa"
LABEL description="Frontend QA agent - accessibility, code review, testing"
