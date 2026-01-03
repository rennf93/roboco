# Python Error Handling

## Never Bare Except

Always catch specific exceptions:

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

## Custom Exceptions

Define domain-specific exceptions:

```python
class TaskError(Exception):
    """Base exception for task operations."""

class TaskNotFoundError(TaskError):
    """Task does not exist."""

class TaskAlreadyClaimedError(TaskError):
    """Task is already claimed."""

# Usage
if task is None:
    raise TaskNotFoundError(f"Task {task_id} not found")
```

## Preserve Exception Chain

When re-raising:

```python
# Good - preserves chain
try:
    result = await external_api.call()
except ExternalAPIError as e:
    raise ServiceError("External API failed") from e

# Bad - loses traceback
except ExternalAPIError:
    raise ServiceError("External API failed")
```

## Structured Logging

Use structlog, NEVER print:

```python
import structlog
logger = structlog.get_logger(__name__)

# Good
logger.info(
    "Task completed",
    task_id=task.id,
    duration_ms=elapsed,
)

# Bad - NEVER use print
print(f"Task {task.id} completed")
```

## Validation at Boundaries

Validate external input at API boundaries:

```python
# API boundary - validate
@router.post("/tasks")
async def create_task(request: TaskCreate) -> TaskResponse:
    # Pydantic validates automatically
    ...

# Internal service - trust validated data
async def process_task(task: Task) -> None:
    # No need to re-validate
    ...
```
