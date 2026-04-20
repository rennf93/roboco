# =============================================================================
# Agent Base Image — multi-stage build
# =============================================================================
# Shared runtime for Claude Code agent containers: Python venv + Node.js +
# @anthropic-ai/claude-code CLI. Specialized images (agent-dev-be, agent-qa-fe,
# etc.) extend this one.
# =============================================================================

# ---- Builder ----------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_HTTP_TIMEOUT=300 \
    UV_CONCURRENT_DOWNLOADS=4 \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-system

# Use base-image Python so venv symlinks stay valid after COPY to runner
# (uv's managed python at /root/.local/share/uv/python isn't carried across).
COPY pyproject.toml uv.lock README.md /app/
RUN uv sync --frozen --no-dev --no-install-project

COPY roboco /app/roboco
RUN uv sync --frozen --no-dev

# ---- Runner -----------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runner

# Runtime deps: Node.js 22 for claude CLI, git for workspace ops, jq for hooks.
# gnupg is only needed to add the NodeSource repo, purged after install.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git gnupg jq \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code@2.1.114 \
    && npm cache clean --force \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/* /root/.npm /tmp/*

RUN useradd -m -s /bin/bash agent

# uv is required at runtime: mcp-config.json spawns every MCP server via
# `uv run python -m roboco.mcp.<server>`. Without it, all 10 roboco MCP
# servers fail to start and the agent falls back to raw HTTP, losing every
# guardrail and inline schema the MCP layer provides.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy the pre-built venv + source from builder, owned by agent user
COPY --from=builder --chown=agent:agent /app /app

# Hook scripts: 0755 so the `agent` user (not root) can read+execute them.
# SessionStart hook runs these as agent; stricter perms break the hook with
# "Permission denied" (exit 126).
COPY docker/scripts/sdk-startup-hook.sh /app/scripts/sdk-startup-hook.sh
COPY docker/scripts/a2a-check-hook.sh /app/scripts/a2a-check-hook.sh
COPY docker/scripts/traceability-hook.sh /app/scripts/traceability-hook.sh
RUN chmod 0755 /app/scripts/*.sh

USER agent

# Workspaces are cloned by the orchestrator (running as root) into a shared
# volume, so inside the agent container they show up owned by a different
# uid. Git 2.35+ refuses to operate on such repos with "dubious ownership"
# until safe.directory is set. `*` trusts everything, which is fine inside
# a per-agent sandbox that only mounts its own workspace.
RUN git config --global --add safe.directory '*'

ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

# Claude Code uses mounted ~/.claude for auth.
# System prompt mounted at /app/system-prompt.md at spawn time.
# MCP config generated at runtime.

EXPOSE 9000

ENTRYPOINT ["claude"]
