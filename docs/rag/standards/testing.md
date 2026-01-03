# Testing Standards

## Coverage Target

Minimum 80% code coverage for all modules.

```bash
# Run with coverage
uv run pytest --cov=roboco --cov-report=term-missing
```

## Async Tests

Use pytest-asyncio:

```python
import pytest

@pytest.mark.asyncio
async def test_fetch_user() -> None:
    user = await fetch_user("test-123")
    assert user.name == "Test User"
```

## Test Structure

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

## Test Factories

Use factory-boy for test data:

```python
from factory import Factory, Faker, LazyAttribute

class TaskFactory(Factory):
    class Meta:
        model = Task

    title = Faker('sentence')
    status = TaskStatus.PENDING
    created_at = LazyAttribute(lambda _: datetime.now(UTC))
```

## Before Submitting to QA

Run full test suite:

```bash
# Backend
uv run pytest
uv run ruff check .
uv run mypy roboco/

# Frontend
pnpm test
pnpm lint
pnpm typecheck
```

## Quality Gates

All tests MUST pass before:
- Submitting for verification
- Creating pull request
- Merging to main
