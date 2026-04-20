# RoboCo

AI Agents Company - A virtual organization of 18 AI agents + 1 human CEO, designed to operate as a complete software development workforce.

## Overview

RoboCo implements a structured organizational hierarchy with formal communication protocols, task management, and quality controls. The system enables a single human (CEO) to orchestrate complex multi-project development at scale.

```
CEO (Renzo - Human)
    │
    └── Board (3 agents)
         ├── Product Owner
         ├── Head of Marketing
         └── Auditor (silent observer, reports to CEO)
              │
              └── Main PM (coordinates all cells)
                   │
                   ├── Backend Cell (5 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter)
                   ├── Frontend Cell (5 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter)
                   └── UX/UI Cell (4 agents: 1 Dev, 1 QA, 1 PM, 1 Documenter)
```

## Project Structure

```
roboco/
├── roboco/                      # Main Python package
│   ├── api/                     # FastAPI routes & schemas
│   │   ├── routes/              # API endpoints (tasks, git, agents, etc.)
│   │   └── schemas/             # Pydantic request/response models
│   ├── services/                # Business logic services
│   │   ├── task.py              # Task lifecycle management
│   │   ├── workspace.py         # Multi-agent workspace management
│   │   ├── messaging.py         # Agent communication
│   │   └── optimal_brain/       # RAG/Knowledge base (piragi)
│   ├── models/                  # Pydantic domain models
│   ├── db/                      # SQLAlchemy ORM & migrations
│   ├── enforcement/             # Task lifecycle state machine
│   ├── runtime/                 # Orchestrator for agent spawning
│   ├── agents/                  # Agent base classes
│   ├── mcp/                     # MCP server implementations
│   └── config.py                # Application configuration
├── agents/
│   ├── blueprints/              # Agent system prompts (18 agents)
│   └── prompts/identities/      # Agent identity files
├── docs/
│   ├── architecture/            # Architecture documentation
│   └── workflows/               # Workflow documentation
├── alembic/                     # Database migrations
├── CLAUDE.md                    # Claude Code guidance
└── docker-compose.yml           # Local development stack
```

## Quick Start

```bash
# Install dependencies
uv sync

# Start PostgreSQL and Redis (Docker)
docker compose up -d

# Run database migrations
uv run alembic upgrade head

# Start the API server
uv run python -m roboco.cli

# Or just the API without orchestrator
uv run uvicorn roboco.api:app --reload --host 0.0.0.0 --port 8000
```

## Configuration

Key environment variables (see `roboco/config.py` for all options):

```bash
# API Server
ROBOCO_HOST=0.0.0.0
ROBOCO_PORT=8000

# Database
ROBOCO_DATABASE_HOST=localhost
ROBOCO_DATABASE_PORT=5432
ROBOCO_DATABASE_NAME=roboco

# Workspaces (Multi-Agent Git)
ROBOCO_WORKSPACES_ROOT=/data/workspaces
ROBOCO_WORKSPACE_AUTO_CLONE=true

# RAG/LLM
ROBOCO_LOCAL_LLM_BASE_URL=http://roboco-ollama:11434/v1
ROBOCO_LOCAL_LLM_MODEL=glm-5:cloud
```

## Multi-Agent Workspace Structure

Each agent gets their own git clone for parallel development:

```
{ROBOCO_WORKSPACES_ROOT}/
└── {project-slug}/
    └── {team}/
        └── {agent-slug}/
            └── [git repository]

Example:
/data/workspaces/roboco/backend/be-dev-1/
/data/workspaces/roboco/backend/be-dev-2/
```

## Task Lifecycle

```
backlog → pending → claimed → in_progress → verifying → awaiting_qa
    ↓                              ↓              ↓           ↓
cancelled                      blocked      needs_revision   awaiting_documentation
                               paused                              ↓
                                                           awaiting_pm_review
                                                                   ↓
                                                           awaiting_ceo_approval
                                                                   ↓
                                                              completed
```

## API Endpoints

| Route Group | Description |
|-------------|-------------|
| `/api/v1/tasks` | Task CRUD, lifecycle, claiming |
| `/api/v1/agents` | Agent management |
| `/api/v1/git` | Git operations (status, commit, push, PR) |
| `/api/v1/test` | Test/lint/format/build commands |
| `/api/v1/sessions` | Communication sessions |
| `/api/v1/messages` | Agent messages |
| `/api/v1/projects` | Project (repo) management |
| `/api/v1/work-sessions` | Git work session tracking |
| `/api/v1/optimal` | RAG/Knowledge base queries |
| `/api/v1/journals` | Agent journals/reflections |

## Development

```bash
# Install dev dependencies
uv sync --all-extras

# Run tests
uv run pytest

# Format and lint
uv run ruff format .
uv run ruff check .
uv run mypy roboco/

# Type checking
uv run mypy roboco/
```

## Core Principles

1. **Everything is a task** - All work is tracked and documented
2. **No work without a task** - Create task record first
3. **No task without acceptance criteria** - How do we know it's done?
4. **No closure without documentation** - Future agents need context
5. **Communication is constant** - Stream reasoning, log everything
6. **The Auditor sees all** - Quality monitored silently
7. **CEO approves major changes** - Human-in-the-loop for critical decisions

## Technology Stack

| Layer | Technology |
|-------|------------|
| API Framework | FastAPI |
| Database | PostgreSQL + SQLAlchemy (async) |
| Vector Store | pgvector (via piragi) |
| Cache/Queue | Redis |
| RAG Library | piragi |
| Embeddings | qwen3-embedding:0.6b (sentence-transformers) |
| Local LLM | Ollama (glm-5:cloud) |
| Cloud LLM | Claude API (Anthropic) |
| Package Manager | uv |

## Status

**Core Infrastructure** (Complete)
- [x] Data models (Pydantic)
- [x] Database ORM (SQLAlchemy async)
- [x] Task lifecycle state machine
- [x] Multi-agent workspace management
- [x] Agent blueprints (18 agents)
- [x] Messaging API
- [x] Task API with full lifecycle
- [x] Git operations API
- [x] Test/CI operations API
- [x] RAG/Knowledge base (piragi + pgvector)
- [x] Agent orchestrator
- [x] CEO approval workflow

**In Progress**
- [x] Frontend panel (vendored under `panel/`, served through nginx on :3000)
- [ ] Full agent autonomy testing

## License

MIT
