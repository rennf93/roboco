# Backend Developer Agent
# Python/FastAPI development tools

FROM roboco-agent-base

USER root

# Backend-specific tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    redis-tools \
    && rm -rf /var/lib/apt/lists/*

USER agent

LABEL role="backend-developer"
LABEL description="Backend developer agent - Python, FastAPI, databases"
