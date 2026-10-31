# =============================================================================
# Agent Base Image — multi-stage build
# =============================================================================
# Shared runtime for Claude Code agent containers: Python venv + Node.js +
# @anthropic-ai/claude-code CLI. Specialized images (agent-dev-be, agent-qa-fe,
# etc.) extend this one.
#
# apt pins name the upstream series Debian bookworm (the base's release, fixed
# by the tag) ships for its whole life, never a Debian revision: point and
# security updates only bump the revision (curl has had 15 since 12.0), so an
# exact pin would fail the build at the next one, while a series pin keeps
# taking those fixes and fails loudly only if the base leaves bookworm. The
# NodeSource and Docker repos are pinned to the major their repo serves.
# ca-certificates is the exception: Debian moves the trust store's own
# version (20230311 -> 20250419) through bookworm-security, so it follows
# that suite instead of a version (a pin would hold back CA removals).
# =============================================================================

# ---- Builder ----------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        'build-essential=12.9*' \
        'git=1:2.39.*' \
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
# The project installs editable (the runner copies /app/roboco itself), so
# UV_COMPILE_BYTECODE skips it: compile it here. unchecked-hash keeps the .pyc
# valid across the COPY --from below regardless of mtimes.
RUN uv sync --frozen --no-dev \
    && python -m compileall -q --invalidation-mode unchecked-hash /app/roboco

# ---- Runner -----------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runner

# pipefail: the signing-key check below pipes gpg into awk, and must fail when
# gpg does.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Runtime deps: Node.js 22 for claude CLI, git for workspace ops, jq for hooks.
# gnupg is only needed to add the NodeSource repo, purged after install.
# NodeSource's signing key (NSolid, 6F71F525...) is fetched over HTTPS and
# accepted only if it is exactly that one primary key, so a swapped or padded
# key file fails the build instead of being trusted. The fingerprint is written
# into the check, not taken from a build arg, so a build can't override it.
# NodeSource's setup_22.x script is not used: it would run a remote script as
# root and trust whatever key it downloads.
# build-essential stays at runtime (NOT purged): when toolchain matching is on,
# uv provisions the agent's workspace against the TARGET project's Python and
# must be able to compile an sdist for any dependency that ships no wheel for
# that interpreter version. Without a compiler the install fails and the agent
# can't run the target's suite. The nodejs pin also refuses bookworm's own
# nodejs 18 should the NodeSource repo be missing.
RUN apt-get update && apt-get install -y --no-install-recommends \
        'curl=7.88.*' \
        ca-certificates/bookworm-security \
        'git=1:2.39.*' \
        'gnupg=2.2.*' \
        'jq=1.6*' \
        'build-essential=12.9*' \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o /tmp/nodesource.asc \
    && [ "$(gpg --show-keys --with-colons /tmp/nodesource.asc | awk -F: '$1=="pub"{n++; getline; f=$10} END{print n":"f}')" = "1:6F71F525282841EEDAF851B42F59B5F99B1BE0B4" ] \
    && gpg --dearmor -o /usr/share/keyrings/nodesource.gpg /tmp/nodesource.asc \
    && printf 'Types: deb\nURIs: https://deb.nodesource.com/node_22.x\nSuites: nodistro\nComponents: main\nSigned-By: /usr/share/keyrings/nodesource.gpg\n' \
        > /etc/apt/sources.list.d/nodesource.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends 'nodejs=22.*' \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/* /tmp/nodesource.asc /root/.gnupg

# Split from the apt layer above: this churns with every claude-code CLI
# release, while the OS/node layer above stays stable across those bumps. The
# version lives in docker/npm/agent-base/package.json, which Dependabot bumps.
COPY docker/npm/agent-base/package.json /tmp/npm-pins/package.json
RUN version="$(node -p "require('/tmp/npm-pins/package.json').dependencies['@anthropic-ai/claude-code']")" \
    && npm install -g "@anthropic-ai/claude-code@${version}" \
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
    && echo "${ENTIRE_SHA256}  /tmp/entire.tar.gz" > /tmp/entire.tar.gz.sha256 \
    && sha256sum -c /tmp/entire.tar.gz.sha256 \
    && mkdir -p /usr/local/lib/entire \
    && tar -xzf /tmp/entire.tar.gz -C /usr/local/lib/entire entire git-remote-entire \
    && ln -s /usr/local/lib/entire/git-remote-entire /usr/local/bin/git-remote-entire \
    && rm -rf /tmp/*
COPY docker/scripts/entire-wrapper.sh /usr/local/bin/entire
# The agent uid/gid are a contract, not a useradd default: the orchestrator
# runs throwaway workspace containers as settings.agent_uid:agent_gid and its
# git user shares this gid (orchestrator.Dockerfile ROBOCO_AGENT_GID).
RUN chmod 0755 /usr/local/bin/entire /usr/local/lib/entire/entire /usr/local/lib/entire/git-remote-entire \
    && groupadd --gid 1000 agent \
    && useradd --uid 1000 --gid 1000 -m -s /bin/bash agent

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
# This image copies ONLY hooks (entrypoint scripts live in the role images),
# plus the umask prefix the binary entrypoints below run through.
COPY docker/scripts/umask-exec.sh /app/scripts/umask-exec.sh
RUN chmod 0755 /app/scripts/hooks/*.sh /app/scripts/umask-exec.sh

# Numeric, so a runtime that cannot read this image's /etc/passwd (or that
# must prove the user is not root before starting it) still resolves it.
USER 1000:1000

# Workspaces are cloned by the orchestrator's git user (uid 1500, this
# image's agent group) into a shared volume, so inside the agent container
# they show up owned by a different uid. Git 2.35+ refuses to operate on such
# repos with "dubious ownership" until safe.directory is set. `*` trusts
# everything, which is fine here: the agent runs only its own git, and every
# repository it can see is one it may write anyway.
#
# Claude Code reads ~/.claude.json (a sibling FILE, not under ~/.claude/). The
# orchestrator bind-mounts the host's copy over this when it exists; pre-create
# an empty config so that when it doesn't, the CLI no longer logs "configuration
# file not found" (3x) at every agent start (audit D-48). The CLI self-heals it.
RUN git config --global --add safe.directory '*' \
    && echo '{}' > /home/agent/.claude.json

# PYTHONUNBUFFERED: flush stdout/stderr immediately so the SDK driver's logs
# (e.g. the intake agent's turn-received / streamed lines) reach `docker logs` in
# real time instead of block-buffering until the container is reaped.
# VIRTUAL_ENV is intentionally NOT baked: it made bare `uv run` in a workspace
# clone warn ("VIRTUAL_ENV=/app/.venv does not match project .venv") on every
# gate run. MCP/SDK pin to /app/.venv via UV_PROJECT_ENVIRONMENT (set in the
# orchestrator + sdk-startup-hook), not VIRTUAL_ENV. PATH keeps gateway tools.
# HYPERFRAMES_NO_TELEMETRY / HYPERFRAMES_NO_UPDATE_CHECK: no image installs
# the HyperFrames CLI, but an agent authoring a video can run it
# (`npx hyperframes check`, as motion/skills/ says). The CLI sends anonymous
# usage events to PostHog unless the first is "1", and checks npm for its own
# updates on every run unless the second is. Set here so every agent image
# inherits both, as motion/package.json's scripts set them.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HYPERFRAMES_NO_TELEMETRY=1 \
    HYPERFRAMES_NO_UPDATE_CHECK=1

# Claude Code uses mounted ~/.claude for auth.
# System prompt mounted at /app/system-prompt.md at spawn time.
# MCP config generated at runtime.

EXPOSE 9000

# No Docker health probe, on purpose. An agent container is one task-scoped
# job, not a service: the orchestrator supervises it directly (heartbeats, the
# stale-claim reaper, `docker inspect` State.Running) and nothing reads a
# Docker health status for it. The one endpoint here, the SDK's GET /health
# on 9000, is served only by some runtimes (claude via sdk-startup-hook.sh,
# intake, opencode); the grok/gemini/codex/kimi/hummin CLIs, the secretaries
# and the live drivers built on this image serve no /health, so a probe here
# would report half the fleet unhealthy.
HEALTHCHECK NONE

ENTRYPOINT ["/app/scripts/umask-exec.sh", "claude"]
