# Backend Cell

## Team: `backend`

## Tech Stack
- **Language**: Python
- **Framework**: FastAPI
- **Database**: PostgreSQL
- **Cache/Queue**: Redis
- **Vector Store**: PostgreSQL + pgvector (via piragi)
- **Container**: Docker

## Your Teammates
- `be-pm` - Backend PM (your PM)
- `be-dev-1`, `be-dev-2` - Backend Developers
- `be-qa` - Backend QA
- `be-doc` - Backend Documenter
- `main-pm` - Main PM (escalation path)

## Development Standards
```bash
# Before any commit
uv run ruff format .
uv run ruff check .
uv run mypy roboco/
uv run pytest

# Coverage target: 80%
```

## Common Patterns
- RESTful API design
- Pydantic models for validation
- SQLAlchemy for ORM
- Dependency injection
- Structured logging with structlog
