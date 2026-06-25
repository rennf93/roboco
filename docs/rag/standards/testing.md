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

## Type Narrowing and mypy

When you assign `None` to an attribute inside a test function, mypy narrows that attribute's type to `None`, and does **not** invalidate this narrowing after a function call — even when the called function takes the object as `Any` or modifies it.

**Problem:** This causes mypy to treat subsequent assertions as unreachable, failing the quality gate.

```python
# ❌ BAD: mypy narrows notes to None and treats the assertion as unreachable
def test_example() -> None:
    t = _Task()
    t.notes = None  # mypy narrows type to None
    process(t)      # Even though process may write to t.notes
    assert t.notes is not None  # [unreachable] — mypy sees this as always False
```

**Solution:** Use a helper class whose `__init__` declares the attribute with its full union type, so mypy uses the declared type (not a narrowed literal) when accessed in your test:

```python
# ✅ GOOD: Annotation-typed class preserves union type
class _TaskWithNoNotes:
    """Variant where notes starts as None (no prior state)."""
    
    def __init__(self) -> None:
        self.id = uuid4()
        self.notes: dict[str, Any] | None = None  # Declared as union, not narrowed
        
def test_example() -> None:
    t = _TaskWithNoNotes()  # Use the helper instead
    process(t)
    assert t.notes is not None  # ✅ Reachable — mypy sees the union type
```

## Quality Gates

All tests MUST pass before:
- Submitting for verification
- Creating pull request
- Merging to main
