# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**RoboCo** is an AI Agentic Company - a virtual organization of 18 AI agents + 1 human CEO, designed to operate as a complete software development workforce. The system implements a structured organizational hierarchy with formal communication protocols, task management, and quality controls.

### Core Architecture

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

### Hardware Infrastructure

- **Olares One (Powerhouse)**: Intel Ultra 9 + RTX 5090, runs Claude Code instances and AI inference
- **UGREEN NAS (Warehouse)**: 36TB RAID6, hosts PostgreSQL, Redis, Qdrant (vector DB)
- **Pi Cluster (Operations)**: Monitoring, notifications, smart home

## Internal Services (To Be Built)

| Service | Purpose |
|---------|---------|
| **Messaging API** | Agent-to-agent communication, channels, sessions, WebSocket streaming |
| **Optimal API** | RAG queries, knowledge base, prompt optimization, token management |
| **Journal API** | Agent personal logs, reflections, growth tracking |
| **Task API** | Task CRUD, status management, kanban views |

## Development Standards

### Python (Backend)
```bash
# Package manager
uv

# Before any commit
uv run ruff format .
uv run ruff check .
uv run mypy src/
uv run pytest

# Coverage target: 80%
```

### TypeScript (Frontend)
```bash
# Package manager
pnpm

# Before any commit
pnpm format
pnpm lint
pnpm typecheck
pnpm test

# Coverage target: 80%
```

### Git Workflow

**Branch naming:**
- `feature/{task-id}-{description}`
- `fix/{task-id}-{description}`
- `refactor/{task-id}-{description}`
- `docs/{task-id}-{description}`

**Commit format:**
```
{type}({scope}): {description}

{body}

Task: {task-id}
Co-authored-by: {agent-name}
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`

## Task Lifecycle

Every piece of work follows this wrapper:

1. **SCAN** - Check for pending/ongoing tasks
2. **CLAIM** - Lock and take ownership
3. **UNDERSTAND** - Read requirements, ask questions (DO NOT PROCEED until clear)
4. **PLAN** - Break down, identify dependencies
5. **EXECUTE** - Do the work, commit frequently
6. **VERIFY** - Self-check against acceptance criteria
7. **NOTES** - Document journey, create handoff
8. **CLOSE** - Cleanup, return to SCAN

**Task states:** `pending` → `claimed` → `in_progress` → `blocked/paused` → `verifying` → `awaiting_qa` → `awaiting_documentation` → `completed`

## Task Directory Structure

```
.tasks/
├── index.md              # Master task index
├── templates/            # Task templates by type
├── active/               # In-progress tasks
│   └── TASK-XXX-name/
│       ├── README.md     # Status, criteria, quick context
│       ├── plan.md       # Implementation plan
│       ├── journal.md    # Agent journey notes
│       ├── decisions.md  # Decision rationale
│       ├── blockers.md   # Current impediments
│       └── handoff.md    # For Documenter
├── completed/            # Archived by month
└── blocked/              # Waiting on blockers
```

## Communication Model

**Communication** = constant stream (always flowing, logged, observed)
**Notifications** = formal signals (require acknowledgment, sent by PMs/Board only)

### Channel Structure
- Cell channels: `#backend-cell`, `#frontend-cell`, `#uxui-cell`
- Cross-cell: `#dev-all`, `#qa-all`, `#pm-all`, `#doc-all`
- Management: `#main-pm-board`, `#board-private`
- Special: `#announcements` (read-only except Board/Main PM), `#all-hands`

The Auditor has silent read access to ALL channels.

## Key Principles

1. **Everything is a task** - All work is tracked and documented
2. **No work without a task** - Create task record first
3. **No task without acceptance criteria** - How do we know it's done?
4. **No closure without documentation** - Future agents need context
5. **Communication is constant** - Stream reasoning, log everything
6. **State is sacred** - If interrupted, state must be recoverable
7. **The Auditor sees all** - Quality monitored silently

## Context Restoration Protocol

When resuming a task:

1. Read task record: `README.md` → `plan.md` → `journal.md` → `decisions.md` → `blockers.md`
2. Review artifacts and related commits
3. Query knowledge base for similar past tasks
4. Add to journal: "Resuming task. Context restored from records."

## Technology Stack

| Layer | Technology |
|-------|------------|
| API Framework | FastAPI |
| Database | PostgreSQL |
| Cache/Queue | Redis |
| Vector DB | Qdrant |
| Container Runtime | Docker + Docker Compose |
| Cloud LLM | Claude API |
| Local LLM | Ollama / vLLM |
| Embeddings | text-embedding-3-small / local |
| Frontend | React / Next.js (future) |

## Implementation Phases

The project follows a phased approach:

- **Phase 0**: Foundation (hardware, Docker, networking)
- **Phase 1**: Core Services (Messaging API, Task API, agent framework)
- **Phase 2**: Communication (WebSocket, transcription, notifications)
- **Phase 3**: Intelligence (RAG, Journal API, knowledge indexing)
- **Phase 4**: Agents (all 17 agent types, cell deployment)
- **Phase 5**: Management (Kanban UIs, dashboards)
- **Phase 6**: Polish (performance, documentation)

## Blueprint Reference

The complete system design is documented in `HOMELAB_TEAM_V0.md`, which contains:
- Organizational structure and role descriptions
- Communication matrix and notification permissions
- Data models (Task, Agent, Session, Message, Channel, Notification, Journal)
- API endpoint specifications
- Kanban board designs
- Security and access control model
- Configuration templates
