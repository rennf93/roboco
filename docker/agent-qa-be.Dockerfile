# Backend QA Agent
# Testing and quality assurance tools for backend

FROM roboco-agent-base

USER root

# QA tools for backend
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

USER agent

# Python testing tools are already in pyproject.toml (pytest, coverage, etc.)

LABEL role="backend-qa"
LABEL description="Backend QA agent - testing, code review, quality assurance"
