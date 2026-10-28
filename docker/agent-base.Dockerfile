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

# UV_COMPILE_BYTECODE: every spawn is a fresh container, so without shipped
# .pyc each agent's four MCP gateway servers recompile the whole venv + roboco
# in parallel at startup (a cold flow_server import measured 8.5s on the NAS,
# 2026-10-15). opencode starts its model loop without waiting for them and
# marked all four failed - the agent ran with no gateway tools.
ENV UV_HTTP_TIMEOUT=300 \
    UV_CONCURRENT_DOWNLOADS=4 \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-system \
    UV_COMPILE_BYTECODE=1

# Use base-image Python so venv symlinks stay valid after COPY to runner
# (uv's managed python at /root/.local/share/uv/python isn't carried across).
COPY pyproject.toml uv.lock README.md /app/
RUN uv sync --frozen --no-dev --no-install-project

COPY roboco /app/roboco
RUN uv sync --frozen --no-dev
# The project installs editable (the runner copies /app/roboco itself), so
# UV_COMPILE_BYTECODE skips it: compile it here. unchecked-hash keeps the .pyc
# valid across the COPY --from below regardless of mtimes.
RUN python -m compileall -q --invalidation-mode unchecked-hash /app/roboco

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

# Entire CLI (entire.io): records Claude Code sessions as git checkpoints.
# Pinned by version AND by sha256 written here (not fetched from the release,
# so a tampered release can't swap both). The real binary sits off PATH; the
# `entire` on PATH is RoboCo's wrapper, which no-ops every Entire hook unless
# the container was spawned with ROBOCO_ENTIRE_ENABLED=true.
ARG ENTIRE_VERSION=0.12.2
ARG ENTIRE_SHA256_AMD64=bfc5c8d5b599f328775b89b735db020c963e307b6a324fc30ce371fe8fa789e8
ARG ENTIRE_SHA256_ARM64=f08a1b8c6352d3f78076268dc67b0b273d7f62dbab94b15aec028e7b4257ad0c
ARG TARGETARCH
RUN case "$TARGETARCH" in \
        amd64) ENTIRE_SHA256="$ENTIRE_SHA256_AMD64" ;; \
        arm64) ENTIRE_SHA256="$ENTIRE_SHA256_ARM64" ;; \
        *) echo "unsupported TARGETARCH: '$TARGETARCH'" >&2; exit 1 ;; \
    esac \
    && curl -fsSL --connect-timeout 15 --retry 5 --retry-all-errors --retry-delay 2 \
        "https://github.com/entireio/cli/releases/download/v${ENTIRE_VERSION}/entire_linux_${TARGETARCH}.tar.gz" \
        -o /tmp/entire.tar.gz \
    && printf '%s  %s\n' "${ENTIRE_SHA256}" /tmp/entire.tar.gz > /tmp/entire.sha256 \
    && sha256sum -c /tmp/entire.sha256 \
    && mkdir -p /usr/local/lib/entire \
    && tar -xzf /tmp/entire.tar.gz -C /usr/local/lib/entire entire git-remote-entire \
    && ln -s /usr/local/lib/entire/git-remote-entire /usr/local/bin/git-remote-entire \
    && rm -rf /tmp/*
COPY docker/scripts/entire-wrapper.sh /usr/local/bin/entire
RUN chmod 0755 /usr/local/bin/entire /usr/local/lib/entire/entire /usr/local/lib/entire/git-remote-entire

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
COPY docker/scripts/hooks/sdk-startup-hook.sh /app/scripts/hooks/sdk-startup-hook.sh
COPY docker/scripts/hooks/a2a-check-hook.sh /app/scripts/hooks/a2a-check-hook.sh
COPY docker/scripts/hooks/bash-guard-hook.sh /app/scripts/hooks/bash-guard-hook.sh
COPY docker/scripts/hooks/post-tool-budget-hook.sh /app/scripts/hooks/post-tool-budget-hook.sh
COPY docker/scripts/hooks/usage-report-hook.sh /app/scripts/hooks/usage-report-hook.sh
COPY docker/scripts/hooks/stop-hook.sh /app/scripts/hooks/stop-hook.sh
COPY docker/scripts/hooks/user-prompt-hook.sh /app/scripts/hooks/user-prompt-hook.sh
COPY docker/scripts/hooks/pre-compact-hook.sh /app/scripts/hooks/pre-compact-hook.sh
COPY docker/scripts/hooks/session-end-hook.sh /app/scripts/hooks/session-end-hook.sh
COPY docker/scripts/hooks/fable-stop-gate-hook.sh /app/scripts/hooks/fable-stop-gate-hook.sh
COPY docker/scripts/hooks/fable-bash-discipline-hook.sh /app/scripts/hooks/fable-bash-discipline-hook.sh
COPY docker/scripts/hooks/fable-honesty-nudge-hook.sh /app/scripts/hooks/fable-honesty-nudge-hook.sh
COPY docker/scripts/hooks/fable-prompt-nudge-hook.sh /app/scripts/hooks/fable-prompt-nudge-hook.sh
COPY docker/scripts/hooks/fable-precompact-hook.sh /app/scripts/hooks/fable-precompact-hook.sh
# This image copies ONLY hooks (entrypoint scripts live in the role images).
RUN chmod 0755 /app/scripts/hooks/*.sh

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
