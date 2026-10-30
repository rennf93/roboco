# =============================================================================
# Orchestrator — multi-stage build
# =============================================================================
# Builder stage compiles the Python venv; runner stage is slim Debian with
# docker-cli (needed to spawn agent containers through the Docker gate's
# socket, docker/docker-gate/) and the pre-built venv copied in.
#
# apt pins follow agent-base.Dockerfile's scheme: the upstream series bookworm
# ships for its whole life (never a Debian revision, which every point or
# security update replaces), the major for the NodeSource and Docker repos,
# and ca-certificates by suite (bookworm-security), never by version.
#
# The image runs as root, deliberately: the orchestrator holds the Docker
# socket, repairs workspace group ownership with fchown, and owns the relays
# under /data/private. Every process it starts in an agent workspace runs as
# the git user through the set-id helper /usr/local/libexec/roboco/as-git-user
# (docker/as-git-user/as-git-user.c, compiled in the builder stage), and
# ROBOCO_WORKSPACE_GIT_DROP=required makes that fail closed: a missing or
# refusing helper raises, and the api and dispatcher refuse to start unless a
# process started through it is the git user with no capability. The kernel
# ignores set-id bits under no_new_privs, so the orchestrator services must
# not get `security_opt: no-new-privileges`. See
# .claude/rules/workspaces-and-git.md.
# =============================================================================

# Entire CLI pin (stages below). Same pin as agent-base.Dockerfile: version
# AND sha256 written here, not fetched from the release.
ARG ENTIRE_VERSION=0.12.2
ARG ENTIRE_SHA256_AMD64=bfc5c8d5b599f328775b89b735db020c963e307b6a324fc30ce371fe8fa789e8
ARG ENTIRE_SHA256_ARM64=f08a1b8c6352d3f78076268dc67b0b273d7f62dbab94b15aec028e7b4257ad0c

# The workspace git user (uid) and the agent images' group (gid). The builder
# compiles both into the git-user helper; the runner creates the user and
# group and exports them as settings.git_user_uid / agent_gid.
ARG ROBOCO_GIT_USER_UID=1500
ARG ROBOCO_AGENT_GID=1000

# ---- Builder ----------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        'build-essential=12.9*' \
        'git=1:2.39.*' \
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

# The git-user exec helper: static (the runner has no compiler or libc
# headers), with the git user's uid and gid compiled in. The source refuses
# to compile with either unset or 0.
ARG ROBOCO_GIT_USER_UID
ARG ROBOCO_AGENT_GID
COPY docker/as-git-user/as-git-user.c /tmp/as-git-user.c
RUN mkdir -p /out \
    && gcc -static -O2 -std=c11 -Wall -Wextra -Wpedantic -Werror \
        -fstack-protector-strong -D_FORTIFY_SOURCE=2 \
        -DGIT_USER_UID="${ROBOCO_GIT_USER_UID}" -DAGENT_GID="${ROBOCO_AGENT_GID}" \
        -o /out/as-git-user /tmp/as-git-user.c

# ---- Entire CLI -------------------------------------------------------------
# Entire (entire.io) records agent sessions as git checkpoints. Agents run its
# session hooks in their own containers; its commit hooks run in the throwaway
# workspace container (roboco/services/entire_hooks.py), started from this
# image. BuildKit fetches the releases and checks their sha256 itself (`ADD
# --checksum`, a mismatch fails the build): no package manager or downloader
# in the stage. Both architectures' tarballs are fetched, each against its own
# pinned sum, and the RUN unpacks TARGETARCH's: a stage picked by
# `FROM ...-${TARGETARCH}` would, for an architecture with no such stage, pull
# that name from a registry instead of failing.
FROM python:3.13-slim-bookworm AS entire
ARG ENTIRE_VERSION
ARG ENTIRE_SHA256_AMD64
ARG ENTIRE_SHA256_ARM64
ARG TARGETARCH
ADD --checksum=sha256:${ENTIRE_SHA256_AMD64} \
    https://github.com/entireio/cli/releases/download/v${ENTIRE_VERSION}/entire_linux_amd64.tar.gz \
    /tmp/entire_linux_amd64.tar.gz
ADD --checksum=sha256:${ENTIRE_SHA256_ARM64} \
    https://github.com/entireio/cli/releases/download/v${ENTIRE_VERSION}/entire_linux_arm64.tar.gz \
    /tmp/entire_linux_arm64.tar.gz
RUN case "$TARGETARCH" in \
        amd64|arm64) ;; \
        *) echo "unsupported TARGETARCH: '$TARGETARCH'" >&2; exit 1 ;; \
    esac \
    && mkdir -p /usr/local/lib/entire \
    && tar --no-same-owner -xzf "/tmp/entire_linux_${TARGETARCH}.tar.gz" \
        -C /usr/local/lib/entire entire

# ---- Runner -----------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runner

# pipefail: the signing-key checks below pipe gpg into awk, and must fail when
# gpg does.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Runtime apt deps: docker-cli (spawn agents), git (workspace ops),
# make (backstop for projects whose CI commands use make targets), Node.js 22
# via NodeSource. Debian's distro nodejs is v18, but current pnpm requires
# Node >=22.13 — installing v18 made `pnpm install` fail in Node/TS workspaces
# (the nodejs pin now refuses it outright). docker-ce-cli stays on the major
# the NAS daemon runs.
# libatomic1: the pnpm a project pins via `packageManager` (pnpm 11's
# @pnpm/exe) is a native binary linked against libatomic.so.1; without it
# every pnpm command in such a project exits 127.
# curl/gnupg/lsb-release are only needed to add the repos, then purged.
# The NodeSource (NSolid, 6F71F525...) and Docker (9DC85822...) signing keys
# are fetched over HTTPS and each accepted only if it is exactly that one
# primary key, so a swapped or padded key file fails the build instead of
# being trusted. The fingerprints are written into the checks, not taken from
# build args, so a build can't override them. NodeSource's setup_22.x script
# is not used: it would run a remote script as root and trust whatever key it
# downloads.
RUN apt-get update && apt-get install -y --no-install-recommends \
        'curl=7.88.*' \
        ca-certificates/bookworm-security \
        'gnupg=2.2.*' \
        'lsb-release=12.0*' \
        'git=1:2.39.*' \
        'make=4.3*' \
        'libatomic1=12.2.*' \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o /tmp/nodesource.asc \
    && [ "$(gpg --show-keys --with-colons /tmp/nodesource.asc | awk -F: '$1=="pub"{n++; getline; f=$10} END{print n":"f}')" = "1:6F71F525282841EEDAF851B42F59B5F99B1BE0B4" ] \
    && gpg --dearmor -o /usr/share/keyrings/nodesource.gpg /tmp/nodesource.asc \
    && printf 'Types: deb\nURIs: https://deb.nodesource.com/node_22.x\nSuites: nodistro\nComponents: main\nSigned-By: /usr/share/keyrings/nodesource.gpg\n' \
        > /etc/apt/sources.list.d/nodesource.sources \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /tmp/docker.asc \
    && [ "$(gpg --show-keys --with-colons /tmp/docker.asc | awk -F: '$1=="pub"{n++; getline; f=$10} END{print n":"f}')" = "1:9DC858229FC7DD38854AE2D88D81803C0EBFCD88" ] \
    && gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg /tmp/docker.asc \
    && DEBIAN_CODENAME=$(lsb_release -cs) \
    && if [ "$DEBIAN_CODENAME" = "trixie" ]; then DEBIAN_CODENAME="bookworm"; fi \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian ${DEBIAN_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends 'nodejs=22.*' 'docker-ce-cli=5:29.*' \
    && apt-get purge -y --auto-remove curl gnupg lsb-release \
    && rm -rf /var/lib/apt/lists/* /tmp/nodesource.asc /tmp/docker.asc /root/.gnupg

# pnpm lets the orchestrator pre-install the frontend/panel cell's workspace
# deps (`pnpm install`) the same way uv handles Python cells. Without it the
# dep-install step gracefully skips (WorkspaceService._run_dep_install catches
# the missing-tool OSError) and the fe-dev re-installs per task. node/npm come
# from the NodeSource layer above. The version lives in
# docker/npm/orchestrator/package.json, which Dependabot bumps.
COPY docker/npm/orchestrator/package.json /tmp/npm-pins/package.json
RUN version="$(node -p "require('/tmp/npm-pins/package.json').dependencies.pnpm")" \
    && npm install -g "pnpm@${version}" \
    && npm cache clean --force \
    && rm -rf /root/.npm /tmp/*

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
# runs as `roboco-git` (roboco/services/workspace_git.py through the
# as-git-user helper): not root, not the agent uid (on the NAS that is the
# host user who owns the provider credentials mounted here), primary group =
# the agent images' gid so both sides write group-writable files the other
# can edit. The uid/gid must match settings.git_user_uid / agent_gid (env set
# below); the startup self-test refuses a mismatch.
#
# The helper is set-user-id and set-group-id to that user, never to root, so
# a bug in it reaches no more than the git user. Mode 6750: only root and the
# agent group may run it.
#
# Root runs git only in its own repositories under /data/private (the relay,
# read, release and scratch clones), with private.gitconfig as its global
# config (safe.directory=*: both deploy colors share those repositories and
# need not share a uid); the image sets no global safe.directory. The git
# user's global config (workspace.gitconfig), and the ONLY hooks directory
# any orchestrator-side git runs, live root-owned in /opt/roboco/git too.
ARG ROBOCO_GIT_USER_UID
ARG ROBOCO_AGENT_GID
RUN groupadd --gid "${ROBOCO_AGENT_GID}" agent \
    && useradd --uid "${ROBOCO_GIT_USER_UID}" --gid "${ROBOCO_AGENT_GID}" \
        --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin \
        roboco-git
RUN --mount=type=bind,from=builder,source=/out/as-git-user,target=/tmp/as-git-user \
    install -D -o "${ROBOCO_GIT_USER_UID}" -g "${ROBOCO_AGENT_GID}" -m 6750 \
        /tmp/as-git-user /usr/local/libexec/roboco/as-git-user
COPY docker/git-support /opt/roboco/git
RUN chown -R root:root /opt/roboco/git && chmod -R u=rwX,go=rX /opt/roboco/git
ENV ROBOCO_GIT_USER_UID=${ROBOCO_GIT_USER_UID} \
    ROBOCO_AGENT_GID=${ROBOCO_AGENT_GID} \
    ROBOCO_WORKSPACE_GIT_DROP=required

# PYTHONUNBUFFERED: flush stdout/stderr immediately so structured logs reach
# `docker logs` in real time. Without it Python block-buffers stdout (it's a pipe,
# not a TTY) and lines arrive in large delayed chunks — which made live log
# diagnosis impossible during the intake smoke tests.
ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# The api and dispatcher roles both serve GET /health on 8000; this is the
# probe and the timing their compose services pin. Two runs of this image
# serve no HTTP and turn it off: the indexer role (`healthcheck: disable` in
# both compose files) and the throwaway workspace containers
# (`--no-healthcheck`, roboco/services/workspace_container.py).
HEALTHCHECK --interval=10s --timeout=5s --start-period=90s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

# Smart dispatcher spawns agents on-demand. Override with --spawn to pre-start.
ENTRYPOINT ["python", "-m", "roboco.cli"]
CMD []
