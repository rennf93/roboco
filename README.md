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
├── roboco/              # Main Python package
│   ├── models/              # Pydantic data models
│   ├── db/                  # SQLAlchemy ORM & database
│   ├── api/                 # FastAPI routes (coming soon)
│   └── config.py            # Application configuration
├── agents/blueprints/       # Agent system prompts (16 agents)
├── .tasks/                  # Task management system
│   ├── templates/           # Task templates by type
│   ├── active/              # In-progress tasks
│   ├── completed/           # Archived tasks
│   └── initiatives/         # Multi-task initiatives
├── CLAUDE.md                # Claude Code guidance
└── HOMELAB_TEAM_V0.md       # System blueprint
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
uv run uvicorn roboco.api:app --reload
```

## Development

```bash
# Install dev dependencies
uv sync --all-extras

# Run tests
uv run pytest

# Format and lint
uv run ruff format .
uv run ruff check .
uv run mypy src/
```

## Core Principles

1. **Everything is a task** - All work is tracked and documented
2. **No work without a task** - Create task record first
3. **No task without acceptance criteria** - How do we know it's done?
4. **No closure without documentation** - Future agents need context
5. **Communication is constant** - Stream reasoning, log everything
6. **The Auditor sees all** - Quality monitored silently

## Technology Stack

| Layer | Technology |
|-------|------------|
| API Framework | FastAPI |
| Database | PostgreSQL + SQLAlchemy |
| Cache/Queue | Redis |
| Vector DB | Qdrant |
| LLM | Claude API |
| Package Manager | uv |

## Status

**Phase 1: Core Services** (In Progress)
- [x] Data models (Pydantic)
- [x] Database ORM (SQLAlchemy)
- [x] Configuration management
- [x] Agent blueprints (16 agents)
- [x] Task templates
- [ ] Messaging API
- [ ] Task API
- [ ] Agent orchestration

## License

MIT
