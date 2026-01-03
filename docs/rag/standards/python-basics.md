# Python Coding Standards

## Package Manager

Use `uv` for all Python operations.

```bash
# Add dependency
uv add package-name

# Add dev dependency
uv add --dev package-name

# Sync dependencies
uv sync

# Run command
uv run pytest
```

## Before Every Commit

```bash
uv run ruff format .      # Format code
uv run ruff check .       # Lint
uv run mypy roboco/       # Type check
uv run pytest             # Tests
```

## Type Hints Required

All functions MUST have type hints:

```python
# Good
async def fetch_user(user_id: UUID) -> User | None:
    ...

# Bad - no type hints
def fetch_user(user_id):
    ...
```

## Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Classes | PascalCase | `TaskService` |
| Functions | snake_case | `get_user` |
| Variables | snake_case | `user_id` |
| Constants | SCREAMING | `MAX_RETRIES` |
| Private | Leading `_` | `_cache` |

## Line Length

Maximum 88 characters (Black default).

## Imports

Sorted order: stdlib, third-party, local.

```python
import asyncio
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from roboco.models import Task
from roboco.services import TaskService
```

## Async by Default

ALL I/O operations must be async:

```python
# Good
async def fetch_user(user_id: str) -> User:
    return await db.users.get(user_id)

# Bad - blocking
def fetch_user(user_id: str) -> User:
    return db.users.get(user_id)  # Blocks!
```

## Concurrent Operations

Use `asyncio.gather` for independent async calls:

```python
# Good - parallel
task, comments = await asyncio.gather(
    get_task(task_id),
    get_comments(task_id),
)

# Bad - sequential
task = await get_task(task_id)
comments = await get_comments(task_id)  # Waits unnecessarily
```
