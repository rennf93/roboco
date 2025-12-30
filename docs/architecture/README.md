# Architecture Documentation

This directory contains detailed architecture documentation for the RoboCo system.

## Documents

| Document | Description |
|----------|-------------|
| [Task Lifecycle](./task_lifecycle.md) | Task states, transitions, and workflow enforcement |
| [Data Model](./data_model.md) | Core entities: Task, Agent, Project, Session, Message, WorkSession |
| [API Overview](./api_overview.md) | High-level API structure and endpoint categories |
| [Workspaces](./workspaces.md) | Multi-agent workspace architecture for parallel development |

## Quick Reference

### System Overview

RoboCo is an AI Agentic Company - a virtual organization of 18 AI agents + 1 human CEO. The system implements:

- **Organizational Hierarchy**: CEO, Board (3 agents), Main PM, and 3 Cell teams (Backend, Frontend, UX/UI)
- **Task Management**: Full lifecycle from backlog to completion with QA and documentation phases
- **Git Integration**: Per-agent workspaces, branch management, PR workflows
- **Knowledge Base**: RAG-powered semantic search across code, docs, decisions, and learnings
- **Communication**: Channel-based messaging with sessions and scoped contexts

### Core Technology Stack

| Layer | Technology |
|-------|------------|
| API Framework | FastAPI |
| Database | PostgreSQL |
| Cache/Queue | Redis |
| Vector DB | Qdrant (via pgvector) |
| Container Runtime | Docker + Docker Compose |
| Cloud LLM | Claude API |
| Local LLM | Ollama / vLLM |
| Embeddings | text-embedding-3-small / local |

### Key Design Principles

1. **Everything is a task** - All work is tracked and documented
2. **No work without a task** - Create task record first
3. **No task without acceptance criteria** - How do we know it's done?
4. **No closure without documentation** - Future agents need context
5. **State is sacred** - If interrupted, state must be recoverable
6. **Communication is constant** - Stream reasoning, log everything
7. **The Auditor sees all** - Quality monitored silently

## Related Documentation

- [CLAUDE.md](../../CLAUDE.md) - Project instructions and coding standards
- [HOMELAB_TEAM_V0.md](../../HOMELAB_TEAM_V0.md) - Complete system design blueprint
