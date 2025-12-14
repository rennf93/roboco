FROM debian:bookworm-slim

# Install dependencies + Docker CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    python3 \
    python3-pip \
    python3-venv \
    gnupg \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (for spawning agent containers)
RUN curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22 (for building agent image)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install uv for Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy project files
COPY roboco /app/roboco
COPY agents /app/agents
COPY docker /app/docker
COPY pyproject.toml uv.lock alembic.ini README.md /app/
COPY alembic /app/alembic

# Install Python dependencies
RUN uv python install 3.13 && uv sync --frozen --python 3.13

# Expose API port
EXPOSE 8000

# Start orchestrator WITHOUT spawning agents - the smart dispatcher will spawn
# agents on-demand when work is available (avoiding wasteful spawns).
# To manually spawn agents at startup, override: docker run ... roboco-orchestrator --spawn main-pm
ENTRYPOINT ["uv", "run", "python", "-m", "roboco.cli"]
CMD []
