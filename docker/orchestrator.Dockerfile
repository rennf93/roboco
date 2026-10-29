# =============================================================================
# Orchestrator — multi-stage build
# =============================================================================
# Builder stage compiles the Python venv; runner stage is slim Debian with
# docker-cli (needed to spawn agent containers over the mounted docker socket)
# and the pre-built venv copied in.
# =============================================================================

# ---- Builder ----------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# py-spy: a standalone Rust binary with no Python runtime of its own, for
# live-profiling a stuck orchestrator (`docker compose exec orchestrator
# py-spy dump --pid 1`) without restarting it. `uv tool install` keeps it out
# of the project venv/lock (never a roboco dependency); its bin-dir entry is
# a symlink into the isolated tool venv, so resolve that with `readlink -f`
# before copying, so the runner stage only gets the plain binary, not a link
# into a tool-venv tree that stage never copies.
RUN uv tool install py-spy \
    && install -m755 "$(readlink -f "$(uv tool dir --bin)/py-spy")" /usr/local/bin/py-spy

WORKDIR /app

ENV UV_HTTP_TIMEOUT=300 \
    UV_CONCURRENT_DOWNLOADS=4 \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-system

# Dependency layer first so source changes don't invalidate the big install.
# --python-preference=only-system forces uv to use the base-image's /usr/local
# Python (shipped with python:3.13-slim-bookworm) instead of downloading its
# own — otherwise the venv symlinks into /root/.local/share/uv/python/... and
# breaks when COPY --from=builder only copies /app.
COPY pyproject.toml uv.lock README.md /app/
RUN uv sync --frozen --no-dev --no-install-project

# Project layer
COPY roboco /app/roboco
COPY agents /app/agents
COPY docs /app/docs
COPY alembic.ini /app/
COPY alembic /app/alembic
RUN uv sync --frozen --no-dev

# ---- Entire CLI -------------------------------------------------------------
# Entire (entire.io) records agent sessions as git checkpoints. Agents run its
# session hooks in their own containers; its commit hooks run in the throwaway
# workspace container (roboco/services/entire_hooks.py), started from this
# image. Same pin as agent-base.Dockerfile: version AND sha256 written here.
FROM python:3.13-slim-bookworm AS entire

ARG ENTIRE_VERSION=0.12.2
ARG ENTIRE_SHA256_AMD64=bfc5c8d5b599f328775b89b735db020c963e307b6a324fc30ce371fe8fa789e8
ARG ENTIRE_SHA256_ARM64=f08a1b8c6352d3f78076268dc67b0b273d7f62dbab94b15aec028e7b4257ad0c
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && case "$TARGETARCH" in \
        amd64) ENTIRE_SHA256="$ENTIRE_SHA256_AMD64" ;; \
        arm64) ENTIRE_SHA256="$ENTIRE_SHA256_ARM64" ;; \
        *) echo "unsupported TARGETARCH: '$TARGETARCH'" >&2; exit 1 ;; \
    esac \
    && curl -fsSL --connect-timeout 15 --retry 5 --retry-all-errors --retry-delay 2 \
        "https://github.com/entireio/cli/releases/download/v${ENTIRE_VERSION}/entire_linux_${TARGETARCH}.tar.gz" \
        -o /tmp/entire.tar.gz \
    && echo "${ENTIRE_SHA256}  /tmp/entire.tar.gz" | sha256sum -c - \
    && mkdir -p /usr/local/lib/entire \
    && tar --no-same-owner -xzf /tmp/entire.tar.gz -C /usr/local/lib/entire entire

# ---- Runner -----------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runner

# Runtime apt deps: docker-cli (spawn agents), git (workspace ops),
# make (backstop for projects whose CI commands use make targets), Node.js 22
# via NodeSource. Debian's distro nodejs is v18, but current pnpm requires
# Node >=22.13 — installing v18 made `pnpm install` fail in Node/TS workspaces.
# libatomic1: the pnpm a project pins via `packageManager` (pnpm 11's
# @pnpm/exe) is a native binary linked against libatomic.so.1; without it
# every pnpm command in such a project exits 127.
# curl/gnupg/lsb-release are only needed to add the repos, then purged.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg lsb-release git make libatomic1 \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && DEBIAN_CODENAME=$(lsb_release -cs) \
    && if [ "$DEBIAN_CODENAME" = "trixie" ]; then DEBIAN_CODENAME="bookworm"; fi \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian ${DEBIAN_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && apt-get purge -y --auto-remove curl gnupg lsb-release \
    && rm -rf /var/lib/apt/lists/*

# pnpm lets the orchestrator pre-install the frontend/panel cell's workspace
# deps (`pnpm install`) the same way uv handles Python cells. Without it the
# dep-install step gracefully skips (WorkspaceService._run_dep_install catches
# the missing-tool OSError) and the fe-dev re-installs per task. node/npm come
# from the NodeSource layer above; pnpm matches how agent-dev-fe installs it.
RUN npm install -g pnpm

WORKDIR /app

# Copy the already-built venv + app tree from builder.
# .venv first (invalidated only by pyproject.toml/uv.lock changes via the
# builder's own dep-then-project split), source dirs last so an app-code-only
# change doesn't bust the much larger .venv layer's cache.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/pyproject.toml /app/uv.lock /app/README.md /app/
COPY --from=builder /app/roboco /app/roboco
COPY --from=builder /app/agents /app/agents
COPY --from=builder /app/docs /app/docs
COPY --from=builder /app/alembic.ini /app/
COPY --from=builder /app/alembic /app/alembic

# uv is needed at runtime: WorkspaceService runs `uv sync` to pre-install
# Python cell deps, and CI commands shell out to `uv run`. The builder stage
# has it at /usr/local/bin/uv; carry it into the runner so it's on PATH.
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv

# py-spy for live profiling (docker compose exec orchestrator py-spy dump
# --pid 1). Needs SYS_PTRACE, granted via cap_add on the orchestrator
# service in the compose files.
COPY --from=builder /usr/local/bin/py-spy /usr/local/bin/py-spy

# Entire CLI, deliberately off PATH: only RoboCo's Entire step runs it, by
# absolute path. On PATH, the wrapper hooks Entire installs in a clone's
# .git/hooks would start it from any project hook run here.
COPY --from=entire /usr/local/lib/entire/entire /usr/local/lib/entire/entire

# Workspace git never runs as root: every agent container mounts every clone
# read-write, so a clone's hooks/config could run anything git starts. It
# runs as `roboco-git` (roboco/services/workspace_git.py via setpriv): not
# root, not the agent uid (on the NAS that is the host user who owns the
# provider credentials mounted here), primary group = the agent images' gid
# so both sides write group-writable files the other can edit. The uid/gid
# must match settings.git_user_uid / agent_gid (env set below).
#
# Root runs git only in its own repositories under /data/private (the relay,
# read and release clones), so root keeps git's "dubious ownership" check:
# no global safe.directory. The git user's global config, and the ONLY hooks
# directory any orchestrator-side git runs, live root-owned in /opt/roboco/git.
ARG ROBOCO_GIT_USER_UID=1500
ARG ROBOCO_AGENT_GID=1000
RUN groupadd --gid "${ROBOCO_AGENT_GID}" agent \
    && useradd --uid "${ROBOCO_GIT_USER_UID}" --gid "${ROBOCO_AGENT_GID}" \
        --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin \
        roboco-git \
    && command -v setpriv
COPY docker/git-support /opt/roboco/git
RUN chown -R root:root /opt/roboco/git && chmod -R u=rwX,go=rX /opt/roboco/git
ENV ROBOCO_GIT_USER_UID=${ROBOCO_GIT_USER_UID} \
    ROBOCO_AGENT_GID=${ROBOCO_AGENT_GID}

# PYTHONUNBUFFERED: flush stdout/stderr immediately so structured logs reach
# `docker logs` in real time. Without it Python block-buffers stdout (it's a pipe,
# not a TTY) and lines arrive in large delayed chunks — which made live log
# diagnosis impossible during the intake smoke tests.
ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Smart dispatcher spawns agents on-demand. Override with --spawn to pre-start.
ENTRYPOINT ["python", "-m", "roboco.cli"]
CMD []
