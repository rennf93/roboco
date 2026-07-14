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
# Before any commit — use the Makefile, never raw `uv run`/`pip`/`conda`/`poetry`.
# The Makefile sets UV_NO_SYNC=1 + a private UV_CACHE_DIR to prevent venv
# corruption; bare `uv run` bypasses both.
make lint        # ruff format + ruff check + mypy + vulture (formats in place)
make gate        # fast pre-submit: ruff format --check + ruff check + mypy + xenon
make quality     # full merge gate (lint+types+tests+cov+xenon+bandit+audit+...)
make test        # pytest with coverage

# Coverage target: 80%
```

## Common Patterns
- RESTful API design
- Pydantic models for validation
- SQLAlchemy for ORM
- Dependency injection
- Structured logging with structlog
