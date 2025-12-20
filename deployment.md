# RoboCo Deployment Guide

Deploy RoboCo on your server/NAS.

## Prerequisites

| Software | Purpose |
|----------|---------|
| Docker | All services (PostgreSQL, Redis, Orchestrator, Agents) |
| Claude Code CLI | Authenticate once on host (creates ~/.claude) |

```bash
# Install and authenticate Claude Code (one time)
npm install -g @anthropic-ai/claude-code
claude  # Login via browser
```

## Quick Start (NAS/Server)

Everything runs in Docker - no need to install Python/uv on the host.

```bash
# 1. Clone the project
git clone <repo-url> roboco
cd roboco

# 2. Configure environment
cp .env.example .env

# 3. Set host paths for your NAS (IMPORTANT!)
# Edit .env and set:
#   ROBOCO_HOST_PROJECT_DIR=/volume1/roboco
#   ROBOCO_HOST_CLAUDE_DIR=/root/.claude  (or your user's home)

# 4. Start everything (PostgreSQL + Redis + Orchestrator)
docker compose up -d

# 5. View logs
docker compose logs -f orchestrator
```

## Architecture

```
Your NAS/Server
│
├── docker compose up -d
│   │
│   ├── roboco-postgres (pgvector)
│   ├── roboco-redis
│   └── roboco-orchestrator
│       │
│       ├── Runs FastAPI on port 8000
│       ├── Builds roboco-agent image (once)
│       └── Spawns agent containers:
│           ├── roboco-agent-main-pm
│           ├── roboco-agent-be-dev-1
│           ├── roboco-agent-be-qa
│           └── ... (each mounts ~/.claude for auth)
│
├── ~/.claude/              ← Your Claude Code auth (from host)
│
└── ./data/                 ← Persistent data
    ├── postgres/
    ├── redis/
    └── mcp-configs/
```

## Configuration

### Environment Variables (.env)

```bash
# Host paths - REQUIRED for NAS deployment
# These tell the orchestrator where to find files on the HOST
ROBOCO_HOST_PROJECT_DIR=/volume1/roboco
ROBOCO_HOST_CLAUDE_DIR=/root/.claude
ROBOCO_DATA_DIR=./data

# Claude Code auth directory (mounted into containers)
CLAUDE_AUTH_DIR=/root/.claude

# Database (defaults work for docker compose)
ROBOCO_DATABASE_HOST=roboco-postgres
ROBOCO_DATABASE_PORT=5432
ROBOCO_DATABASE_USER=roboco
ROBOCO_DATABASE_PASSWORD=roboco
ROBOCO_DATABASE_NAME=roboco

# Redis (defaults work for docker compose)
ROBOCO_REDIS_HOST=roboco-redis
ROBOCO_REDIS_PORT=6379
```

### Customizing Agent Spawn

By default, the orchestrator spawns `main-pm`, `be-dev-1`, and `be-qa`. To change this:

```bash
# Option 1: Override in docker-compose.yml
docker compose up -d --scale orchestrator=0
docker compose run orchestrator --spawn main-pm fe-dev-1 fe-qa

# Option 2: Edit docker-compose.yml command section
# Uncomment and modify the command line
```

## Verification

```bash
# API health
curl http://localhost:8000/health

# Orchestrator status (shows running containers)
curl http://localhost:8000/api/v1/orchestrator/status | jq

# List all RoboCo containers
docker ps --filter "name=roboco"

# View orchestrator logs
docker compose logs -f orchestrator

# View agent logs
docker logs -f roboco-agent-main-pm
```

## Data Persistence

All data is persisted to the host:

| Container Path | Host Path |
|----------------|-----------|
| postgres data | `./data/postgres/` |
| redis data | `./data/redis/` |
| MCP configs | `./data/mcp-configs/` |

For NAS RAID protection, set `ROBOCO_DATA_DIR` to your RAID volume:

```bash
ROBOCO_DATA_DIR=/volume1/roboco/data
```

## Troubleshooting

### Orchestrator can't spawn agents

```bash
# Check Docker socket is mounted
docker compose logs orchestrator | grep -i docker

# Verify host paths are set correctly
docker compose exec orchestrator env | grep ROBOCO_HOST

# Check if agent image was built
docker images | grep roboco-agent
```

### Agent containers exit immediately

```bash
# Check Claude auth is mounted
docker logs roboco-agent-main-pm

# Verify ~/.claude exists on host
ls -la ~/.claude/

# Re-authenticate if needed
claude
```

### Database connection failed

```bash
docker compose ps postgres
docker compose logs postgres
```

### API not responding

```bash
curl http://localhost:8000/health
docker compose logs orchestrator
```

## Stopping

```bash
# Stop all services
docker compose down

# Stop all agent containers (if needed)
docker ps --filter "name=roboco-agent" -q | xargs docker stop

# Full cleanup (removes volumes)
docker compose down -v
```

## Development Mode (Host)

For local development without Docker orchestrator:

```bash
# Start only infrastructure
docker compose up -d postgres redis

# Install Python dependencies
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# Run migrations
uv run alembic upgrade head

# Start orchestrator directly
uv run python -m roboco.cli --spawn main-pm be-dev-1 be-qa
```
