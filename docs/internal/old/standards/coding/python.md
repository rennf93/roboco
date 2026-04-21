# Python Coding Standards

Comprehensive standards for Python development in the RoboCo system. These standards are enforced through automated tooling (see [Tooling Enforcement](#tooling-enforcement) section).

---

## Table of Contents

1. [Code Style](#code-style)
2. [Type Safety](#type-safety)
3. [Error Handling](#error-handling)
4. [Data Validation](#data-validation)
5. [Async Patterns](#async-patterns)
6. [Testing](#testing)
7. [Dependencies](#dependencies)
8. [Tooling Enforcement](#tooling-enforcement)
9. [Code Complexity](#code-complexity)
10. [Security](#security)

---

## Code Style

### PY-001: Use Type Hints

**Severity:** ERROR
**Tools:** mypy, ruff

All function signatures MUST include type hints for parameters and return values.

```python
# Good
def process_task(task_id: str, priority: int = 1) -> TaskResult:
    ...

async def fetch_user(user_id: UUID) -> User | None:
    ...

# Bad - Missing type hints
def process_task(task_id, priority=1):
    ...
```

### PY-002: Docstrings Required

**Severity:** WARNING
**Tools:** ruff (D100-D417)

All public functions, classes, and modules MUST have docstrings following Google style.

```python
def calculate_metrics(data: list[float]) -> MetricsResult:
    """Calculate statistical metrics from data points.

    Args:
        data: List of numeric values to analyze.

    Returns:
        MetricsResult containing mean, median, and std dev.

    Raises:
        ValueError: If data is empty.
    """
```

### PY-003: Import Organization

**Severity:** ERROR
**Tools:** ruff (I001-I002)

Imports MUST be sorted in this order: stdlib, third-party, local. Use `ruff` to enforce.

```python
# Good
import asyncio
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.models import Task, User
from roboco.services import TaskService
```

### PY-004: Line Length

**Severity:** ERROR
**Tools:** ruff (E501)

Maximum line length is 88 characters (Black default). Use line breaks for long expressions.

```python
# Good - Multi-line function call
result = await service.create_task(
    title=request.title,
    description=request.description,
    assigned_to=agent_id,
    priority=request.priority,
)

# Good - Multi-line string
error_message = (
    f"Task {task_id} cannot be claimed: "
    f"current status is {task.status}, expected 'pending'"
)
```

### PY-005: Naming Conventions

**Severity:** ERROR
**Tools:** ruff (N801-N818)

| Type | Convention | Example |
|------|------------|---------|
| Classes | PascalCase | `TaskService`, `UserModel` |
| Functions | snake_case | `get_user`, `process_task` |
| Variables | snake_case | `user_id`, `task_count` |
| Constants | SCREAMING_SNAKE | `MAX_RETRIES`, `DEFAULT_TIMEOUT` |
| Private | Leading underscore | `_internal_method`, `_cache` |
| Type Variables | PascalCase | `T`, `TaskT`, `ResponseT` |

---

## Type Safety

### PY-010: Strict Mypy Configuration

**Severity:** ERROR
**Tools:** mypy

The project uses strict mypy configuration. All code MUST pass these checks:

```toml
# pyproject.toml settings (enforced)
[tool.mypy]
python_version = "3.13"
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
no_implicit_optional = true
strict_optional = true
warn_return_any = true
warn_unreachable = true
```

### PY-011: No `Any` Types

**Severity:** ERROR
**Tools:** mypy

Avoid `Any` type. Use `object`, generics, or `TypeVar` instead.

```python
# Bad
def process_data(data: Any) -> Any:
    ...

# Good - Use generics
T = TypeVar('T')
def process_data(data: T) -> T:
    ...

# Good - Use Union for multiple types
def process_data(data: str | bytes) -> ProcessedData:
    ...
```

### PY-012: Use `None` Explicitly

**Severity:** ERROR
**Tools:** mypy

Use `| None` for optional values. Never use implicit optional.

```python
# Bad - Implicit optional
def get_user(user_id: str, cache: dict = None) -> User:
    ...

# Good - Explicit optional
def get_user(user_id: str, cache: dict[str, User] | None = None) -> User:
    ...
```

### PY-013: Use Type Aliases for Complex Types

**Severity:** WARNING
**Tools:** ruff

Create type aliases for complex or repeated types.

```python
# Good - Type aliases improve readability
type TaskCallback = Callable[[Task, TaskStatus], Awaitable[None]]
type SearchResults = list[tuple[str, float, dict[str, Any]]]

async def search_with_callback(
    query: str,
    callback: TaskCallback,
) -> SearchResults:
    ...
```

---

## Error Handling

### PY-020: Specific Exceptions

**Severity:** ERROR
**Tools:** ruff (E722, B001)

NEVER use bare `except:`. Catch specific exceptions.

```python
# Good
try:
    result = await service.process(data)
except ValidationError as e:
    logger.warning("Validation failed", error=str(e))
    raise
except ServiceUnavailableError:
    await retry_with_backoff(service.process, data)

# Bad - NEVER do this
try:
    result = await service.process(data)
except:
    pass
```

### PY-021: Use Custom Exceptions

**Severity:** WARNING
**Tools:** code review

Define domain-specific exceptions for better error handling.

```python
# Good - Custom exceptions
class TaskError(Exception):
    """Base exception for task operations."""

class TaskNotFoundError(TaskError):
    """Task does not exist."""

class TaskAlreadyClaimedError(TaskError):
    """Task is already claimed by another agent."""

# Usage
async def claim_task(task_id: str, agent_id: str) -> Task:
    task = await get_task(task_id)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} not found")
    if task.assigned_to and task.assigned_to != agent_id:
        raise TaskAlreadyClaimedError(f"Task claimed by {task.assigned_to}")
    ...
```

### PY-022: Structured Logging

**Severity:** ERROR
**Tools:** ruff, code review

Use structlog with context for all logging. NEVER use print statements.

```python
# Good
import structlog

logger = structlog.get_logger(__name__)

logger.info(
    "Task completed",
    task_id=task.id,
    duration_ms=elapsed,
    agent_id=agent.id,
)

# Bad - Never use print
print(f"Task {task.id} completed in {elapsed}ms")
```

### PY-023: Re-raise with Context

**Severity:** WARNING
**Tools:** code review

When catching and re-raising, preserve the original exception chain.

```python
# Good - Preserve exception chain
try:
    result = await external_api.call()
except ExternalAPIError as e:
    raise ServiceError("External API failed") from e

# Bad - Loses original traceback
try:
    result = await external_api.call()
except ExternalAPIError:
    raise ServiceError("External API failed")
```

---

## Data Validation

### PY-030: Pydantic Models

**Severity:** ERROR
**Tools:** ruff, mypy

Use Pydantic for all API request/response models and configuration.

```python
from pydantic import BaseModel, Field, field_validator

class TaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    priority: int = Field(default=1, ge=1, le=5)
    tags: list[str] = Field(default_factory=list)

    @field_validator('tags')
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        return [tag.lower().strip() for tag in v]
```

### PY-031: Validate at Boundaries

**Severity:** ERROR
**Tools:** code review

Validate external input at system boundaries. Trust internal data.

```python
# API boundary - validate thoroughly
@router.post("/tasks")
async def create_task(request: TaskCreate) -> TaskResponse:
    # Pydantic validates automatically
    ...

# Internal service - trust validated data
async def process_task(task: Task) -> None:
    # No need to re-validate task.title here
    ...
```

### PY-032: Use Enums for Finite Values

**Severity:** WARNING
**Tools:** ruff

Use Enums for status values, types, and other finite sets.

```python
from enum import StrEnum

class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

# Usage - type-safe comparisons
if task.status == TaskStatus.PENDING:
    ...
```

---

## Async Patterns

### PY-040: Async by Default

**Severity:** ERROR
**Tools:** code review

Use async functions for ALL I/O operations. All database and API calls MUST be async.

```python
# Good - async I/O
async def fetch_user(user_id: str) -> User:
    return await db.users.get(user_id)

async def call_external_api(data: dict) -> Response:
    async with httpx.AsyncClient() as client:
        return await client.post(url, json=data)

# Bad - Blocking I/O
def fetch_user(user_id: str) -> User:
    return db.users.get(user_id)  # Blocking!
```

### PY-041: Use `asyncio.gather` for Concurrent Operations

**Severity:** WARNING
**Tools:** code review

Execute independent async operations concurrently.

```python
# Good - Concurrent execution
async def get_task_details(task_id: str) -> TaskDetails:
    task, comments, history = await asyncio.gather(
        get_task(task_id),
        get_comments(task_id),
        get_history(task_id),
    )
    return TaskDetails(task=task, comments=comments, history=history)

# Bad - Sequential when not needed
async def get_task_details(task_id: str) -> TaskDetails:
    task = await get_task(task_id)
    comments = await get_comments(task_id)  # Waits unnecessarily
    history = await get_history(task_id)    # Waits unnecessarily
    ...
```

### PY-042: Proper Context Manager Usage

**Severity:** ERROR
**Tools:** ruff (ASYNC)

Use async context managers for resources that need cleanup.

```python
# Good - Proper cleanup
async with AsyncSession(engine) as session:
    async with session.begin():
        result = await session.execute(query)

# Good - httpx client
async with httpx.AsyncClient() as client:
    response = await client.get(url)
```

### PY-043: Avoid Blocking in Async Code

**Severity:** ERROR
**Tools:** ruff (ASYNC), bandit

NEVER call blocking functions from async code.

```python
# Bad - Blocks event loop
async def process_file(path: Path) -> str:
    return path.read_text()  # Blocking!

# Good - Use async file I/O
import aiofiles

async def process_file(path: Path) -> str:
    async with aiofiles.open(path) as f:
        return await f.read()

# Good - Run blocking in thread pool
async def process_file(path: Path) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, path.read_text)
```

---

## Testing

### PY-050: Test Coverage

**Severity:** ERROR
**Tools:** pytest-cov

Maintain minimum 80% code coverage for all modules.

```bash
# Run with coverage
uv run pytest --cov=roboco --cov-report=term-missing
```

### PY-051: Async Tests

**Severity:** ERROR
**Tools:** pytest-asyncio

Use pytest-asyncio for testing async code.

```python
import pytest

@pytest.mark.asyncio
async def test_fetch_user() -> None:
    user = await fetch_user("test-123")
    assert user.name == "Test User"
```

### PY-052: Test Structure

**Severity:** WARNING
**Tools:** code review

Follow AAA pattern: Arrange, Act, Assert.

```python
@pytest.mark.asyncio
async def test_task_claim_success() -> None:
    # Arrange
    task = await create_test_task(status=TaskStatus.PENDING)
    agent = await create_test_agent()

    # Act
    claimed_task = await task_service.claim(task.id, agent.id)

    # Assert
    assert claimed_task.status == TaskStatus.CLAIMED
    assert claimed_task.assigned_to == agent.id
```

### PY-053: Use Factories for Test Data

**Severity:** WARNING
**Tools:** code review

Use factory-boy for consistent test data generation.

```python
from factory import Factory, Faker, LazyAttribute

class TaskFactory(Factory):
    class Meta:
        model = Task

    title = Faker('sentence')
    status = TaskStatus.PENDING
    created_at = LazyAttribute(lambda _: datetime.now(UTC))
```

---

## Dependencies

### PY-060: Use UV

**Severity:** ERROR
**Tools:** pyproject.toml

Use `uv` as the package manager. Lock dependencies in `pyproject.toml` and `uv.lock`.

```bash
# Add dependency
uv add package-name

# Add dev dependency
uv add --dev package-name

# Sync dependencies
uv sync
```

### PY-061: Pin Dependencies

**Severity:** WARNING
**Tools:** deptry

Keep `uv.lock` committed. Run `uv lock` when updating dependencies.

### PY-062: Audit Dependencies

**Severity:** ERROR
**Tools:** pip-audit

Run security audits on dependencies regularly.

```bash
# Audit for vulnerabilities
uv run pip-audit
```

---

## Tooling Enforcement

All Python code MUST pass these automated checks before merge.

### Ruff (Linting & Formatting)

```bash
# Format code
uv run ruff format .

# Check linting
uv run ruff check .

# Auto-fix issues
uv run ruff check --fix .
```

**Enabled Rule Sets:**

| Rule | Description |
|------|-------------|
| `E`, `W` | pycodestyle (PEP 8) |
| `F` | Pyflakes (errors) |
| `I` | isort (imports) |
| `B` | flake8-bugbear (common bugs) |
| `C4` | flake8-comprehensions |
| `UP` | pyupgrade (Python upgrades) |
| `ARG` | unused arguments |
| `SIM` | simplification |
| `TCH` | type checking |
| `PTH` | pathlib usage |
| `PL` | Pylint |
| `RUF` | Ruff-specific |

### Mypy (Type Checking)

```bash
uv run mypy roboco/
```

**Configuration (pyproject.toml):**

```toml
[tool.mypy]
python_version = "3.13"
strict = true
plugins = ["pydantic.mypy"]
```

### Vulture (Dead Code)

```bash
uv run vulture roboco/ vulture_whitelist.py
```

Finds unused code. Add false positives to `vulture_whitelist.py`.

### Bandit (Security)

```bash
uv run bandit -r roboco/ -ll
```

Scans for security issues. Severity threshold: medium.

### Radon (Complexity)

```bash
# Cyclomatic complexity
uv run radon cc roboco/ -nc

# Maintainability index
uv run radon mi roboco/ -nc
```

### Xenon (Complexity Thresholds)

```bash
uv run xenon roboco/ --max-absolute B --max-modules A --max-average A
```

**Thresholds:**

| Metric | Maximum | Grade |
|--------|---------|-------|
| Absolute complexity | B | 6-10 |
| Module complexity | A | 1-5 |
| Average complexity | A | 1-5 |

### Deptry (Dependency Analysis)

```bash
uv run deptry .
```

Finds unused, missing, and misplaced dependencies.

---

## Code Complexity

### PY-070: Maximum Cyclomatic Complexity

**Severity:** ERROR
**Tools:** radon, xenon

Functions MUST have cyclomatic complexity <= 10 (grade B or better).

```python
# Bad - Too complex (CC > 10)
def process_request(request: Request) -> Response:
    if request.type == "A":
        if request.priority == 1:
            if request.urgent:
                ...  # Deep nesting = high complexity
    elif request.type == "B":
        ...

# Good - Decomposed into smaller functions
def process_request(request: Request) -> Response:
    handler = get_handler(request.type)
    return handler.process(request)
```

### PY-071: Maximum Function Length

**Severity:** WARNING
**Tools:** code review

Functions SHOULD be <= 50 lines. Consider decomposition if longer.

### PY-072: Maximum Arguments

**Severity:** WARNING
**Tools:** ruff (PLR0913)

Functions SHOULD have <= 5 arguments. Use dataclasses or Pydantic models for more.

```python
# Bad - Too many arguments
def create_task(
    title: str,
    description: str,
    priority: int,
    due_date: datetime,
    assigned_to: str,
    tags: list[str],
    parent_id: str | None,
) -> Task:
    ...

# Good - Use a model
class TaskCreate(BaseModel):
    title: str
    description: str
    priority: int = 1
    due_date: datetime | None = None
    assigned_to: str | None = None
    tags: list[str] = []
    parent_id: str | None = None

def create_task(params: TaskCreate) -> Task:
    ...
```

### PY-073: Avoid Deep Nesting

**Severity:** WARNING
**Tools:** code review

Maximum nesting depth SHOULD be 4 levels. Use early returns and guard clauses.

```python
# Bad - Deep nesting
def process(data: Data) -> Result:
    if data.valid:
        if data.type == "A":
            if data.ready:
                if data.value > 0:
                    return process_a(data)
    return None

# Good - Guard clauses
def process(data: Data) -> Result | None:
    if not data.valid:
        return None
    if data.type != "A":
        return None
    if not data.ready:
        return None
    if data.value <= 0:
        return None
    return process_a(data)
```

---

## Security

### PY-080: No Hardcoded Secrets

**Severity:** BLOCKER
**Tools:** bandit (B105, B106, B107)

NEVER hardcode secrets. Use environment variables.

```python
# Bad - NEVER do this
API_KEY = "sk-abc123xyz789"
DATABASE_URL = "postgresql://user:password@host/db"

# Good - Load from environment
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    api_key: str
    database_url: str

    model_config = {"env_prefix": "ROBOCO_"}
```

### PY-081: Use `usedforsecurity` for Non-Security Hashes

**Severity:** WARNING
**Tools:** bandit (B324)

When using hash functions for non-security purposes, add `usedforsecurity=False`.

```python
# Good - Non-security hash for ID generation
import hashlib

content_hash = hashlib.md5(
    content.encode(),
    usedforsecurity=False
).hexdigest()[:12]
```

### PY-082: SQL Injection Prevention

**Severity:** BLOCKER
**Tools:** bandit (B608)

NEVER construct SQL with string concatenation. Use parameterized queries.

```python
# Bad - SQL injection vulnerability
query = f"SELECT * FROM users WHERE id = '{user_id}'"

# Good - Parameterized query
result = await session.execute(
    select(User).where(User.id == user_id)
)
```

### PY-083: Command Injection Prevention

**Severity:** BLOCKER
**Tools:** bandit (B602, B603, B604)

NEVER pass user input directly to shell commands.

```python
# Bad - Command injection vulnerability
import os
os.system(f"process_file {filename}")

# Good - Use subprocess with list
import subprocess
if not SAFE_FILENAME_PATTERN.match(filename):
    raise ValidationError("Invalid filename")
subprocess.run(["process_file", filename], check=True)
```

### PY-084: No `eval` or `exec`

**Severity:** BLOCKER
**Tools:** bandit (B307)

NEVER use `eval()` or `exec()` on untrusted input.

```python
# Bad - Code injection vulnerability
result = eval(user_input)

# Good - Use ast.literal_eval for safe parsing
import ast
result = ast.literal_eval(user_input)  # Only parses literals
```

---

## Quick Reference

### Before Committing

```bash
# Format
uv run ruff format .

# Lint
uv run ruff check .

# Type check
uv run mypy roboco/

# Dead code
uv run vulture roboco/ vulture_whitelist.py

# Full check (recommended)
make lint
```

### Severity Levels

| Level | Action | Blocks PR |
|-------|--------|-----------|
| BLOCKER | Must fix immediately | Yes |
| ERROR | Must fix before merge | Yes |
| WARNING | Should fix | No |
| INFO | Consider improving | No |

### Rule ID Reference

| Prefix | Category |
|--------|----------|
| PY-00X | Code style |
| PY-01X | Type safety |
| PY-02X | Error handling |
| PY-03X | Data validation |
| PY-04X | Async patterns |
| PY-05X | Testing |
| PY-06X | Dependencies |
| PY-07X | Complexity |
| PY-08X | Security |
