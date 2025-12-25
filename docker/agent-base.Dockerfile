# =============================================================================
# Agent Base Image
# =============================================================================
# Python 3.13 with dev tools for Claude Code agent containers
# =============================================================================

FROM python:3.13-bookworm

# Install Node.js 22 (required for Claude Code CLI)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Install uv for Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Create agent user BEFORE copying files
RUN useradd -m -s /bin/bash agent

# Create app directory and set ownership
WORKDIR /app
RUN chown agent:agent /app

# Copy MCP server code (needed for agent tools) AS agent
COPY --chown=agent:agent roboco /app/roboco
COPY --chown=agent:agent pyproject.toml uv.lock README.md /app/

# Switch to agent user for installing dependencies
USER agent

# Install Python dependencies for MCP servers (as agent)
ENV UV_HTTP_TIMEOUT=300
ENV UV_CONCURRENT_DOWNLOADS=4
RUN uv python install 3.13 && uv sync --frozen --python 3.13

# Claude Code will use mounted ~/.claude for auth
# Blueprints mounted at /app/agents/blueprints
# MCP config generated at runtime

ENTRYPOINT ["claude"]
