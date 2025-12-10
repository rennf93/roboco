# TASK-006: Phase 6 - Polish

## Status
- **State**: completed
- **Priority**: P0
- **Cell**: board

## Dates
- **Created**: 2025-12-09
- **Completed**: 2025-12-09

## Overview
Implement Phase 6 of the RoboCo system per HOMELAB_TEAM_V0.md blueprint (Section 13.8):
- Error handling improvements with custom exceptions
- Structured logging throughout with correlation IDs
- Database migrations with Alembic
- Middleware for request tracking and error handling
- Code quality and consistency

## Acceptance Criteria
- [x] Comprehensive error handling with custom exceptions
- [x] Structured logging with correlation IDs
- [x] Alembic migration for all tables
- [x] Request/response logging middleware
- [x] Error handling middleware with structured responses

## What Was Built

### 1. Custom Exceptions (`roboco/exceptions.py`)

| Exception | Description |
|-----------|-------------|
| `RobocoError` | Base exception with code, message, details |
| `NotFoundError` | Resource not found (404) |
| `AlreadyExistsError` | Resource already exists (409) |
| `ValidationError` | Input validation failed (422) |
| `InvalidStateError` | Invalid state transition (409) |
| `PermissionDeniedError` | No permission for action (403) |
| `AuthenticationError` | Authentication required (401) |
| `TaskError` | Base task error |
| `TaskLifecycleError` | Invalid task state transition |
| `TaskBlockedError` | Task blocked by dependencies |
| `TaskClaimError` | Cannot claim task |
| `AgentError` | Base agent error |
| `AgentNotAvailableError` | Agent offline/unavailable |
| `AgentBusyError` | Agent working on another task |
| `ChannelError` | Base channel error |
| `ChannelAccessDeniedError` | No access to channel |
| `SessionClosedError` | Session is closed |
| `NotificationError` | Notification failed |
| `NotificationPermissionError` | Cannot send notifications |
| `ServiceError` | External service error |
| `DatabaseError` | Database operation failed |
| `LLMError` | LLM service error |
| `RAGError` | RAG service error |

All exceptions include:
- `code`: Machine-readable error code
- `message`: Human-readable description
- `details`: Additional context dict
- `to_dict()`: Convert to API response format

### 2. Middleware (`roboco/api/middleware.py`)

| Component | Description |
|-----------|-------------|
| `CorrelationIdMiddleware` | Adds X-Correlation-ID to requests/responses |
| `RequestLoggingMiddleware` | Logs request/response with timing |
| `roboco_exception_handler` | Handles RobocoError exceptions |
| `generic_exception_handler` | Handles unexpected exceptions |
| `setup_middleware(app)` | Setup function for app |

Features:
- Correlation ID from header or auto-generated
- Stored in request.state.correlation_id
- Bound to structlog context
- Added to all error responses
- Request timing in X-Response-Time-Ms header

### 3. Logging Configuration (`roboco/logging.py`)

| Component | Description |
|-----------|-------------|
| `setup_logging()` | Configure structlog |
| `get_logger(name)` | Get structured logger |
| `LogContext` | Context manager for temp log context |
| `log_operation()` | Create structured log context |

Features:
- Development: Colored console output
- Production: JSON output for log aggregation
- Consistent format across all modules
- Context variables for correlation
- App context (version, environment) in all logs

### 4. Alembic Migration (`alembic/versions/001_initial_schema.py`)

Creates all tables:
- agents
- tasks
- channels
- groups
- sessions
- messages
- notifications
- journals
- journal_entries
- handoffs

With:
- Proper foreign key relationships
- Enum types for status/role fields
- ARRAY columns for lists
- JSON columns for structured data
- Performance indexes for common queries

### 5. Updated Application (`roboco/api/app.py`)

- Logging setup at import time
- Startup/shutdown logging
- Middleware integration
- Error handler registration

## File Structure
```
roboco/
├── __init__.py            # Updated with core exports
├── exceptions.py          # NEW - Custom exception hierarchy
├── logging.py             # NEW - Structured logging config
├── api/
│   ├── app.py             # Updated with middleware
│   └── middleware.py      # NEW - Request/error middleware
└── alembic/versions/
    └── 001_initial_schema.py  # NEW - Initial migration
```

## Usage Examples

### Raising Exceptions
```python
from roboco.exceptions import NotFoundError, TaskLifecycleError

# Resource not found
raise NotFoundError("Task", task_id)

# Invalid state transition
raise TaskLifecycleError(
    task_id=task.id,
    current_status=task.status.value,
    target_status="in_progress",
)
```

### Structured Logging
```python
from roboco.logging import get_logger, LogContext, log_operation

logger = get_logger(__name__)

# Basic logging
logger.info("Task created", task_id=str(task.id), title=task.title)

# With context
with LogContext(task_id=str(task.id), agent_id=str(agent.id)):
    logger.info("Processing task")
    # All logs in this block have task_id and agent_id

# Operation logging
logger.info("Task updated", **log_operation("update", "task", str(task.id)))
```

### Running Migrations
```bash
# Create migration
alembic revision --autogenerate -m "description"

# Run migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

## API Error Response Format
```json
{
    "error": {
        "code": "NOT_FOUND",
        "message": "Task not found: 123e4567-e89b-12d3-a456-426614174000",
        "details": {
            "resource_type": "Task",
            "resource_id": "123e4567-e89b-12d3-a456-426614174000",
            "correlation_id": "abc123..."
        }
    }
}
```

## Quick Context Restore
Phase 6 polish complete. Custom exception hierarchy with 20+ exception types for proper error handling. Structured logging with correlation IDs for request tracing. Middleware for request logging and error handling. Alembic migration with all 10 tables, proper relationships and indexes. All phases (1-6) of the RoboCo system are now implemented.
