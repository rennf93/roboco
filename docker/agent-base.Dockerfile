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

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

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
# build-essential stays at runtime (NOT purged): when toolchain matching is on,
# uv provisions the agent's workspace against the TARGET project's Python and
# must be able to compile an sdist for any dependency that ships no wheel for
# that interpreter version. Without a compiler the install fails and the agent
# can't run the target's suite.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git gnupg jq build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/*

# Split from the apt layer above: this churns with every claude-code CLI
# release, while the OS/node layer above stays stable across those bumps.
RUN npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && rm -rf /root/.npm /tmp/*

RUN useradd -m -s /bin/bash agent

# uv is required at runtime: mcp-config.json spawns every MCP server via
# `uv run python -m roboco.mcp.<server>`. Without it, all 10 roboco MCP
# servers fail to start and the agent falls back to raw HTTP, losing every
# guardrail and inline schema the MCP layer provides.
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app

# Copy the pre-built venv + source from builder, owned by agent user.
# .venv first (invalidated only by pyproject.toml/uv.lock changes via the
# builder's own dep-then-project split), source dirs last so an app-code-only
# change doesn't bust the much larger .venv layer's cache.
COPY --from=builder --chown=agent:agent /app/.venv /app/.venv
COPY --from=builder --chown=agent:agent /app/pyproject.toml /app/uv.lock /app/README.md /app/
COPY --from=builder --chown=agent:agent /app/roboco /app/roboco

# Hook scripts: 0755 so the `agent` user (not root) can read+execute them.
# SessionStart hook runs these as agent; stricter perms break the hook with
# "Permission denied" (exit 126).
COPY docker/scripts/sdk-startup-hook.sh /app/scripts/sdk-startup-hook.sh
COPY docker/scripts/a2a-check-hook.sh /app/scripts/a2a-check-hook.sh
COPY docker/scripts/bash-guard-hook.sh /app/scripts/bash-guard-hook.sh
COPY docker/scripts/post-tool-budget-hook.sh /app/scripts/post-tool-budget-hook.sh
COPY docker/scripts/usage-report-hook.sh /app/scripts/usage-report-hook.sh
COPY docker/scripts/stop-hook.sh /app/scripts/stop-hook.sh
COPY docker/scripts/user-prompt-hook.sh /app/scripts/user-prompt-hook.sh
COPY docker/scripts/pre-compact-hook.sh /app/scripts/pre-compact-hook.sh
COPY docker/scripts/session-end-hook.sh /app/scripts/session-end-hook.sh
COPY docker/scripts/fable-stop-gate-hook.sh /app/scripts/fable-stop-gate-hook.sh
COPY docker/scripts/fable-bash-discipline-hook.sh /app/scripts/fable-bash-discipline-hook.sh
COPY docker/scripts/fable-honesty-nudge-hook.sh /app/scripts/fable-honesty-nudge-hook.sh
COPY docker/scripts/fable-prompt-nudge-hook.sh /app/scripts/fable-prompt-nudge-hook.sh
COPY docker/scripts/fable-precompact-hook.sh /app/scripts/fable-precompact-hook.sh
RUN chmod 0755 /app/scripts/*.sh

USER agent

# Workspaces are cloned by the orchestrator (running as root) into a shared
# volume, so inside the agent container they show up owned by a different
# uid. Git 2.35+ refuses to operate on such repos with "dubious ownership"
# until safe.directory is set. `*` trusts everything, which is fine inside
# a per-agent sandbox that only mounts its own workspace.
RUN git config --global --add safe.directory '*'

# Claude Code reads ~/.claude.json (a sibling FILE, not under ~/.claude/). The
# orchestrator bind-mounts the host's copy over this when it exists; pre-create
# an empty config so that when it doesn't, the CLI no longer logs "configuration
# file not found" (3x) at every agent start (audit D-48). The CLI self-heals it.
RUN echo '{}' > /home/agent/.claude.json

# PYTHONUNBUFFERED: flush stdout/stderr immediately so the SDK driver's logs
# (e.g. the intake agent's turn-received / streamed lines) reach `docker logs` in
# real time instead of block-buffering until the container is reaped.
# VIRTUAL_ENV is intentionally NOT baked: it made bare `uv run` in a workspace
# clone warn ("VIRTUAL_ENV=/app/.venv does not match project .venv") on every
# gate run. MCP/SDK pin to /app/.venv via UV_PROJECT_ENVIRONMENT (set in the
# orchestrator + sdk-startup-hook), not VIRTUAL_ENV. PATH keeps gateway tools.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Claude Code uses mounted ~/.claude for auth.
# System prompt mounted at /app/system-prompt.md at spawn time.
# MCP config generated at runtime.

EXPOSE 9000

ENTRYPOINT ["claude"]
