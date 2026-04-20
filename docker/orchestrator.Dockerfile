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

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

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

# ---- Runner -----------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runner

# Runtime apt deps: docker-cli (spawn agents), git (workspace ops).
# curl/gnupg/lsb-release are only needed to add the docker repo, then purged.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg lsb-release git \
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

WORKDIR /app

# Copy the already-built venv + app tree from builder
COPY --from=builder /app /app

# Orchestrator clones workspaces as root, then chowns them to the agent
# user (uid 1000) so the agent container can read/write. After the chown,
# the orchestrator (still root) needs to run git commands (claim branch
# creation, status, log, fetch) inside those now-1000-owned dirs — git's
# "dubious ownership" check refuses unless safe.directory is set. `*`
# trusts every path, which is fine here since this container only mounts
# the sandboxed /data/workspaces tree.
RUN git config --global --add safe.directory '*'

ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 8000

# Smart dispatcher spawns agents on-demand. Override with --spawn to pre-start.
ENTRYPOINT ["python", "-m", "roboco.cli"]
CMD []
