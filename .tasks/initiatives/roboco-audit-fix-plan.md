# RoboCo Comprehensive Audit & Fix Plan

**Generated:** 2025-12-13
**Auditor:** Claude Code (Opus 4.5)
**Scope:** Full `roboco/` directory (79 Python files, 41 with issues)
**Total Issues:** 358 mypy errors + architectural inconsistencies

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Critical Issues (P0)](#critical-issues-p0)
3. [High Priority Issues (P1)](#high-priority-issues-p1)
4. [Medium Priority Issues (P2)](#medium-priority-issues-p2)
5. [Low Priority Issues (P3)](#low-priority-issues-p3)
6. [Architectural Improvements](#architectural-improvements)
7. [Sprint Plan](#sprint-plan)
8. [File-by-File Fix Reference](#file-by-file-fix-reference)
9. [Testing Strategy](#testing-strategy)
10. [Appendix: Full Error Catalog](#appendix-full-error-catalog)

---

## Executive Summary

### Current State
- **Ruff Linting:** PASS (all style checks pass)
- **Mypy Type Checking:** FAIL (358 errors in 41 files)
- **Runtime Stability:** AT RISK (3 critical missing attributes)

### Issue Distribution
| Severity | Count | Description |
|----------|-------|-------------|
| P0 Critical | 12 | Runtime breaks, missing attributes |
| P1 High | 89 | Type mismatches causing potential bugs |
| P2 Medium | 194 | Missing annotations, weak typing |
| P3 Low | 63 | Style, documentation, minor issues |

### Root Causes
1. **ORM ↔ Pydantic divergence** - Tables and models evolved separately
2. **UUID type handling** - SQLAlchemy UUID vs Python UUID confusion
3. **Incomplete implementations** - Stub methods, missing enum values
4. **Inconsistent patterns** - Service initialization, error handling vary

---

## Critical Issues (P0)

### P0-001: Missing `delivered_at` Attribute on NotificationTable

**Location:** `roboco/services/notification_delivery.py:74`
**Error:** `"NotificationTable" has no attribute "delivered_at"`

**Current Code:**
```python
# notification_delivery.py:74
notification.delivered_at = datetime.now(UTC)
```

**Root Cause:** The `NotificationTable` in `db/tables.py` does not define a `delivered_at` column.

**Fix Required:**
```python
# db/tables.py - Add to NotificationTable class (around line 540)
delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

**Migration Required:** Yes - Alembic migration to add column

**Files Affected:**
- `roboco/db/tables.py` (add column)
- `roboco/models/notification.py` (add field to Pydantic model)

---

### P0-002: Missing `ack_read_at` Attribute on NotificationTable

**Location:** `roboco/services/notification_delivery.py:254`
**Error:** `"NotificationTable" has no attribute "ack_read_at"; maybe "acked_at" or "created_at"?`

**Current Code:**
```python
# notification_delivery.py:254
notification.ack_read_at = now
```

**Root Cause:** The code uses `ack_read_at` but the table has `acked_at` (dict) and `read_by` (list).

**Analysis:**
- `acked_at: dict[str, datetime]` - Tracks when each agent acknowledged
- `read_by: list[UUID]` - Tracks which agents read
- Code seems to want a single datetime for "when was it read"

**Fix Options:**

**Option A (Recommended):** Use existing `acked_at` dict correctly
```python
# notification_delivery.py:254
# Instead of: notification.ack_read_at = now
notification.acked_at[str(agent_id)] = now
```

**Option B:** Add new column for read timestamp
```python
# db/tables.py
read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

**Files Affected:**
- `roboco/services/notification_delivery.py` (fix usage)
- Possibly `roboco/db/tables.py` (if Option B)

---

### P0-003: Missing `HandoffStatus.ACCEPTED` Enum Value

**Location:** `roboco/services/task.py:406`
**Error:** `"type[HandoffStatus]" has no attribute "ACCEPTED"`

**Current Code:**
```python
# task.py:405-406
select(HandoffTable).where(
    HandoffTable.task_id == task_id,
    HandoffStatus.ACCEPTED,  # <-- This value doesn't exist
)
```

**Current HandoffStatus Enum:**
```python
# models/base.py:139-145
class HandoffStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    # Missing: ACCEPTED
```

**Fix Required:**
```python
# models/base.py - Add ACCEPTED status
class HandoffStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    ACCEPTED = "accepted"      # <-- Add this
    COMPLETED = "completed"
```

**Alternative Fix:** If ACCEPTED is meant to be COMPLETED, update task.py:
```python
# task.py:406
HandoffTable.status == HandoffStatus.COMPLETED,
```

**Files Affected:**
- `roboco/models/base.py` (add enum value) OR
- `roboco/services/task.py` (change to COMPLETED)

---

### P0-004: Incorrect `validate_task_ownership` Call Signature

**Location:** `roboco/services/task.py:201`
**Error:** `Unexpected keyword argument "assigned_to" for "validate_task_ownership"; did you mean "task_assigned_to"?`

**Current Code:**
```python
# task.py:201
validate_task_ownership(
    agent_id=str(agent_id),
    task_id=str(task_id),
    assigned_to=str(current_assignee),  # Wrong parameter name
    action="reassign",
)
```

**Expected Signature (enforcement/task_ownership.py:50):**
```python
def validate_task_ownership(
    agent_id: str,
    task_id: str,
    task_assigned_to: str | None,  # Correct parameter name
    action: str,
) -> None:
```

**Fix Required:**
```python
# task.py:201
validate_task_ownership(
    agent_id=str(agent_id),
    task_id=str(task_id),
    task_assigned_to=str(current_assignee),  # Fixed parameter name
    action="reassign",
)
```

**Files Affected:**
- `roboco/services/task.py:201`

---

### P0-005: Abstract Class Instantiation - DeveloperAgent

**Locations:** `roboco/agents/developer.py:680, 706, 732`
**Error:** `Cannot instantiate abstract class "DeveloperAgent" with abstract attributes "_cleanup" and "_initialize"`

**Current Code:**
```python
# developer.py:680
def create_backend_developer(...) -> DeveloperAgent:
    ...
    return DeveloperAgent(config)  # Abstract class!
```

**Root Cause:** `DeveloperAgent` extends `Agent` which has abstract methods that aren't implemented.

**Fix Required:**
```python
# agents/developer.py - Add implementations
class DeveloperAgent(Agent):
    # ... existing code ...

    async def _initialize(self) -> None:
        """Initialize developer-specific resources."""
        self.log.debug("Developer agent initialized")
        # Add any developer-specific initialization

    async def _cleanup(self) -> None:
        """Cleanup developer-specific resources."""
        self.log.debug("Developer agent cleanup")
        # Add any developer-specific cleanup
```

**Files Affected:**
- `roboco/agents/developer.py` (add methods)
- `roboco/agents/documenter.py` (same issue: lines 554, 580, 606)
- `roboco/agents/board.py` (same issue: lines 739, 766, 793)
- `roboco/agents/qa.py` (verify if same issue exists)
- `roboco/agents/pm.py` (verify if same issue exists)

---

### P0-006: Incorrect dataclass `field()` with Mutable Default

**Locations:** 9 occurrences across multiple files
**Error:** `No overload variant of "field" matches argument type "datetime"`

**Current Code Pattern:**
```python
# Wrong - datetime is mutable/callable
@dataclass
class SomeClass:
    timestamp: datetime = field(default=datetime.now(UTC))
```

**Fix Required:**
```python
# Correct - use default_factory for callables
@dataclass
class SomeClass:
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
```

**Files to Fix:**
| File | Line |
|------|------|
| `roboco/services/transcription.py` | 46, 47 |
| `roboco/services/extraction.py` | 152 |
| `roboco/events/bus.py` | 80 |
| `roboco/agents/documenter.py` | 79 |
| `roboco/agents/developer.py` | 50 |
| `roboco/agents/board.py` | 334, 346 |

---

## High Priority Issues (P1)

### P1-001: UUID Type Mismatches (70+ occurrences)

**Pattern:** Services pass Python `uuid.UUID` but ORM returns `sqlalchemy.sql.sqltypes.UUID[Any]`

**Example Error:**
```
services/journal.py:98: error: Argument "id" to "Journal" has incompatible type
"sqlalchemy.sql.sqltypes.UUID[Any]"; expected "uuid.UUID"
```

**Root Cause:** When reading from ORM tables, UUID columns are returned as SQLAlchemy UUID type, not Python UUID.

**Fix Strategy - Create Conversion Utility:**
```python
# roboco/utils/converters.py (new file)
from uuid import UUID as PythonUUID
from typing import Any

def to_python_uuid(value: Any) -> PythonUUID | None:
    """Convert SQLAlchemy UUID to Python UUID."""
    if value is None:
        return None
    if isinstance(value, PythonUUID):
        return value
    return PythonUUID(str(value))

def to_python_uuid_list(values: list[Any]) -> list[PythonUUID]:
    """Convert list of SQLAlchemy UUIDs to Python UUIDs."""
    return [to_python_uuid(v) for v in values if v is not None]
```

**Apply Fix Pattern:**
```python
# Before
Journal(
    id=row.id,  # SQLAlchemy UUID
    agent_id=row.agent_id,
)

# After
from roboco.utils.converters import to_python_uuid

Journal(
    id=to_python_uuid(row.id),
    agent_id=to_python_uuid(row.agent_id),
)
```

**Files Affected (by occurrence count):**
| File | Occurrences |
|------|-------------|
| `services/journal.py` | 16 |
| `services/messaging.py` | 10 |
| `services/kanban.py` | 9 |
| `services/notification_delivery.py` | 4 |
| `services/metrics.py` | 2 |

---

### P1-002: Sequence vs List Type Mismatch

**Location:** `roboco/services/kanban.py` (7 occurrences)
**Error:** `Argument 1 to "_build_flat_board" has incompatible type "Sequence[TaskTable]"; expected "list[TaskTable]"`

**Current Code:**
```python
# kanban.py:120
def _build_flat_board(self, tasks: list[TaskTable], ...) -> KanbanBoard:
```

**Fix Required - Broaden Parameter Type:**
```python
from typing import Sequence

def _build_flat_board(self, tasks: Sequence[TaskTable], ...) -> KanbanBoard:
```

**Or Convert at Call Site:**
```python
# At call site
self._build_flat_board(list(tasks), ...)
```

**Lines to Fix:** 120, 123, 314, 344, 363, 382, 477

---

### P1-003: Missing Return Type Annotations

**Location:** `roboco/runtime/orchestrator.py` and others
**Error:** `Function is missing a return type annotation`

**Files with Missing Annotations:**
| File | Missing Count |
|------|---------------|
| `runtime/orchestrator.py` | 63 |
| `agents/base.py` | ~5 |
| `services/*` | ~10 |

**Fix Pattern:**
```python
# Before
async def _spawn_process(self, agent_id: str, prompt: str | None):
    ...

# After
async def _spawn_process(self, agent_id: str, prompt: str | None) -> None:
    ...
```

---

### P1-004: Returning Any from Typed Functions

**Pattern:** Functions declared with return types but returning `Any`

**Example:**
```python
# llm/toon_adapter.py:73
def encode(self, data: Any) -> str:
    return json.dumps(data)  # json.dumps returns Any
```

**Fix - Add Explicit Cast:**
```python
def encode(self, data: Any) -> str:
    result: str = json.dumps(data)
    return result
```

**Files Affected:**
| File | Lines |
|------|-------|
| `llm/toon_adapter.py` | 73, 93, 103 |
| `services/permissions.py` | 104, 110, 573 |
| `services/optimal.py` | 237, 260 |
| `agents/developer.py` | 596 |
| `agents/documenter.py` | 465, 474, 483, 492, 511 |
| `mcp/notify_server.py` | 421, 461 |
| `mcp/message_server.py` | 257, 264, 435, 444, 486, 495 |

---

### P1-005: FastMCP Agent ID Access Pattern

**Locations:** 4 MCP server files
**Error:** `"FastMCP[Any]" has no attribute "agent_id"`

**Current Pattern:**
```python
# mcp/task_server.py:187
mcp.agent_id  # FastMCP doesn't have this attribute
```

**Fix - Use Closure Variable:**
```python
def create_task_mcp_server(agent_id: str) -> FastMCP:
    mcp = FastMCP("roboco-task")

    @mcp.tool()
    async def roboco_task_scan() -> dict:
        # Use agent_id from closure, not mcp.agent_id
        tasks = await scan_for_agent(agent_id)
        ...
```

**Files to Fix:**
- `roboco/mcp/task_server.py:187`
- `roboco/mcp/notify_server.py:108`
- `roboco/mcp/message_server.py:89`
- `roboco/mcp/journal_server.py:72`

---

### P1-006: Config Decorator Order Issue

**Location:** `roboco/config.py:64, 73, 90, 123`
**Error:** `Decorators on top of @property are not supported`

**Current Code:**
```python
@computed_field
@property
def database_url(self) -> str:
    ...
```

**Fix - Use cached_property or method:**
```python
# Option A: Use functools.cached_property
from functools import cached_property

@cached_property
def database_url(self) -> str:
    ...

# Option B: Convert to regular method
def get_database_url(self) -> str:
    ...
```

---

### P1-007: Extraction Service Content Block Handling

**Location:** `roboco/services/extraction.py:411`
**Error:** `Item "ThinkingBlock" of union has no attribute "text"`

**Current Code:**
```python
# extraction.py:411
for block in response.content:
    text = block.text  # Not all blocks have .text
```

**Fix - Type Guard:**
```python
from anthropic.types import TextBlock

for block in response.content:
    if isinstance(block, TextBlock):
        text = block.text
        ...
```

---

### P1-008: ExtractionResult/ExtractedMessage Missing Fields

**Location:** `roboco/services/extraction.py:422, 436`
**Error:** `Unexpected keyword argument "message_type" for "ExtractedMessage"`

**Current Code:**
```python
# extraction.py:422
ExtractedMessage(
    message_type=msg_type,  # Wrong field name?
    metadata=meta,          # Field doesn't exist?
)
```

**Fix - Check Model Definition:**
```python
# Check roboco/models/message.py for correct field names
# Likely should be:
ExtractedMessage(
    type=msg_type,      # Correct field name
    # Remove metadata or add to model
)
```

---

## Medium Priority Issues (P2)

### P2-001: Event Bus Redis Check Pattern

**Location:** Multiple service files
**Issue:** Accessing private `_redis` member

**Current Code:**
```python
# messaging.py:351
if bus._redis:  # Accessing private member
```

**Fix - Add Public Method:**
```python
# events/bus.py
class EventBus:
    def is_connected(self) -> bool:
        """Check if Redis is connected."""
        return self._redis is not None and self._pubsub is not None

# Usage
if bus.is_connected():
    await bus.publish(...)
```

---

### P2-002: Logging Processor Type Mismatch

**Location:** `roboco/logging.py:48`
**Error:** List item has incompatible type for processors

**Fix:**
```python
# Cast or adjust processor signature to match expected type
from typing import cast
processors = cast(list[Processor], [...])
```

---

### P2-003: Database Base Unreachable Statement

**Location:** `roboco/db/base.py:129`
**Error:** `Statement is unreachable`

**Current Code:**
```python
async def init_db() -> None:
    ...
    return  # Early return
    logger.info("...")  # Unreachable
```

**Fix:** Remove unreachable code or reorder

---

### P2-004: ORM vs Pydantic Model Divergence

**Issue:** JSON storage in ORM doesn't match structured Pydantic models

| ORM Field | ORM Type | Pydantic Type | Issue |
|-----------|----------|---------------|-------|
| `Agent.model_config` | `dict[str, Any]` | `ModelConfig` | No validation |
| `Agent.permissions` | `dict[str, Any]` | `AgentPermissions` | No validation |
| `Agent.metrics` | `dict[str, Any]` | `AgentMetrics` | No validation |
| `Task.plan` | `dict[str, Any]` | `TaskPlan` | No validation |
| `Task.execution_log` | `dict[str, Any]` | `ExecutionLog` | No validation |
| `Notification.acked_at` | `dict[str, Any]` | `dict[str, datetime]` | Datetime serialization |
| `Handoff.required_docs` | `list[dict]` | `list[DocumentationItem]` | No validation |

**Fix Strategy - Create Mapper Layer:**
```python
# roboco/mappers/__init__.py
# roboco/mappers/agent.py
from roboco.db.tables import AgentTable
from roboco.models.agent import Agent, ModelConfig, AgentPermissions, AgentMetrics

def agent_table_to_model(row: AgentTable) -> Agent:
    """Map ORM row to Pydantic model with validation."""
    return Agent(
        id=to_python_uuid(row.id),
        name=row.name,
        model=ModelConfig(**row.model_config),
        permissions=AgentPermissions(**row.permissions),
        metrics=AgentMetrics(**row.metrics),
        ...
    )
```

---

### P2-005: Circular Import - Journal ↔ Optimal

**Location:** `roboco/services/journal.py:72`

**Current Pattern:**
```python
async def _get_optimal_service(self) -> Any:
    if self._optimal_service is None:
        from roboco.services.optimal import get_optimal_service  # Lazy import
        self._optimal_service = await get_optimal_service()
    return self._optimal_service
```

**Fix - Use Dependency Injection:**
```python
class JournalService:
    def __init__(
        self,
        session: AsyncSession,
        optimal_service: OptimalService | None = None,  # Optional DI
    ):
        self.session = session
        self._optimal_service = optimal_service
```

---

### P2-006: Board Agent timedelta.hours Issue

**Location:** `roboco/agents/board.py:530`
**Error:** `"timedelta" has no attribute "hours"`

**Current Code:**
```python
# board.py:530
duration = some_timedelta.hours  # Wrong!
```

**Fix:**
```python
# timedelta doesn't have .hours, use total_seconds()
duration_hours = some_timedelta.total_seconds() / 3600
```

---

## Low Priority Issues (P3)

### P3-001: Weak Dict Typing in Models

**Issue:** Using `list[dict[str, str]]` instead of typed models

**Locations in Pydantic models:**
- `Task.risks: list[dict[str, str]]` → Should be `list[RiskItem]`
- `Task.open_questions: list[dict[str, str | bool]]` → Should be `list[Question]`
- `Handoff.commits: list[dict[str, str]]` → Should be `list[CommitSummary]`
- `Handoff.key_decisions: list[dict[str, str]]` → Should be `list[Decision]`
- `Handoff.gotchas: list[dict[str, str]]` → Should be `list[Gotcha]`

**Fix - Create Supporting Models:**
```python
# models/task.py
class RiskItem(RobocoBase):
    risk: str
    mitigation: str
    severity: str = "medium"

class Question(RobocoBase):
    question: str
    answer: str | None = None
    answered: bool = False
```

---

### P3-002: Inconsistent Naming Patterns

| Pattern | Examples | Recommendation |
|---------|----------|----------------|
| Date field naming | `last_activity` vs `last_activity_at` | Standardize to `*_at` |
| Boolean fields | `is_archived`, `self_verified` | Standardize to `is_*` |
| Status tracking | Enums vs booleans | Prefer enums |

---

### P3-003: Missing Validators

**Recommended Validators to Add:**

```python
# models/task.py
@field_validator('assigned_to')
def validate_assigned_when_claimed(cls, v, info):
    """Ensure assigned_to is set when status is CLAIMED or IN_PROGRESS."""
    if info.data.get('status') in [TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS]:
        if v is None:
            raise ValueError("assigned_to required for claimed/in-progress tasks")
    return v

# models/channel.py
@field_validator('writers')
def validate_writers_are_members(cls, v, info):
    """Ensure writers are subset of members."""
    members = set(info.data.get('members', []))
    writers = set(v)
    if not writers.issubset(members):
        raise ValueError("Writers must be channel members")
    return v
```

---

### P3-004: Agent Blueprint Loading Brittleness

**Location:** All agent factory functions

**Current Issue:**
```python
# Relative path, may not exist
blueprint_path = Path("agents/blueprints/board/product-owner.md")
if blueprint_path.exists():
    # Load
else:
    system_prompt = "You are..."  # Minimal fallback
```

**Fix - Use Package Resources:**
```python
from importlib.resources import files

def load_blueprint(agent_type: str, role: str) -> str:
    """Load blueprint with proper fallback."""
    try:
        blueprint = files("roboco.agents.blueprints") / agent_type / f"{role}.md"
        return blueprint.read_text()
    except FileNotFoundError:
        logger.warning(f"Blueprint not found for {agent_type}/{role}")
        return get_default_prompt(agent_type, role)
```

---

## Architectural Improvements

### A1: Service Initialization Standardization

**Current State:** Mixed patterns
- Constructor injection with session
- Singleton factories (sync and async)
- App state storage

**Recommended Pattern:**
```python
# roboco/services/base.py
from abc import ABC, abstractmethod

class BaseService(ABC):
    """Base class for all services."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.log = structlog.get_logger().bind(service=self.__class__.__name__)

    @abstractmethod
    async def health_check(self) -> bool:
        """Check service health."""
        pass

# Usage
class TaskService(BaseService):
    async def health_check(self) -> bool:
        try:
            await self.session.execute(select(1))
            return True
        except Exception:
            return False
```

---

### A2: Result Type for Error Handling

**Current State:** Mixed error handling (None, exceptions, silent failures)

**Recommended Pattern:**
```python
# roboco/types.py
from typing import TypeVar, Generic
from dataclasses import dataclass

T = TypeVar('T')
E = TypeVar('E', bound=Exception)

@dataclass
class Ok(Generic[T]):
    value: T

@dataclass
class Err(Generic[E]):
    error: E
    message: str

Result = Ok[T] | Err[E]

# Usage
async def get_task(task_id: UUID) -> Result[Task, NotFoundError]:
    task = await self.session.get(TaskTable, task_id)
    if task is None:
        return Err(NotFoundError("task", task_id), f"Task {task_id} not found")
    return Ok(task_to_model(task))
```

---

### A3: Transaction Management

**Current State:** No explicit transaction boundaries

**Recommended Pattern:**
```python
# roboco/db/transaction.py
from contextlib import asynccontextmanager

@asynccontextmanager
async def transaction(session: AsyncSession):
    """Explicit transaction context."""
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise

# Usage
async with transaction(self.session) as tx:
    await tx.execute(...)
    await tx.execute(...)
    # Auto-commit on success, rollback on error
```

---

### A4: Mapper Layer for ORM ↔ Pydantic

**Structure:**
```
roboco/
├── mappers/
│   ├── __init__.py
│   ├── agent.py      # AgentTable ↔ Agent
│   ├── task.py       # TaskTable ↔ Task
│   ├── channel.py    # ChannelTable ↔ Channel
│   ├── message.py    # MessageTable ↔ ExtractedMessage
│   ├── session.py    # SessionTable ↔ Session
│   ├── notification.py
│   ├── journal.py
│   └── handoff.py
```

**Pattern:**
```python
# mappers/agent.py
def to_model(row: AgentTable) -> Agent:
    """Convert ORM to Pydantic with validation."""
    ...

def to_table(model: Agent) -> dict[str, Any]:
    """Convert Pydantic to ORM-compatible dict."""
    ...

def update_table(row: AgentTable, update: AgentUpdate) -> None:
    """Apply update to ORM row."""
    ...
```

---

## Sprint Plan

### Sprint 1: Critical Fixes (P0)
**Duration:** 1-2 days
**Goal:** Eliminate runtime breaks

| Task | File(s) | Effort |
|------|---------|--------|
| Add `delivered_at` to NotificationTable | `db/tables.py`, `models/notification.py` | 30m |
| Fix `ack_read_at` usage | `services/notification_delivery.py` | 30m |
| Add `ACCEPTED` to HandoffStatus | `models/base.py` | 15m |
| Fix `validate_task_ownership` call | `services/task.py` | 15m |
| Add `_initialize`/`_cleanup` to agents | `agents/*.py` | 2h |
| Fix `field(default=datetime)` | 9 locations | 1h |
| Generate Alembic migration | `alembic/` | 30m |

**Deliverable:** All P0 issues resolved, mypy errors < 300

---

### Sprint 2: Type Safety (P1)
**Duration:** 2-3 days
**Goal:** Fix type mismatches

| Task | File(s) | Effort |
|------|---------|--------|
| Create UUID converter utility | `utils/converters.py` (new) | 30m |
| Fix UUID conversions in services | `services/*.py` | 4h |
| Fix Sequence vs list types | `services/kanban.py` | 1h |
| Add missing return type annotations | `runtime/orchestrator.py` | 2h |
| Fix FastMCP agent_id pattern | `mcp/*.py` | 1h |
| Fix config decorator order | `config.py` | 30m |
| Fix extraction content block handling | `services/extraction.py` | 1h |

**Deliverable:** All P1 issues resolved, mypy errors < 150

---

### Sprint 3: Medium Priority (P2)
**Duration:** 2-3 days
**Goal:** Improve code quality

| Task | File(s) | Effort |
|------|---------|--------|
| Add `is_connected()` to EventBus | `events/bus.py` | 30m |
| Update event bus usage | `services/*.py` | 1h |
| Fix logging processor types | `logging.py` | 30m |
| Remove unreachable code | `db/base.py` | 15m |
| Create mapper layer (basic) | `mappers/` (new) | 4h |
| Fix timedelta.hours | `agents/board.py` | 15m |
| Refactor journal ↔ optimal dependency | `services/journal.py` | 1h |

**Deliverable:** All P2 issues resolved, mypy errors < 50

---

### Sprint 4: Polish & Architecture (P3 + A*)
**Duration:** 3-5 days
**Goal:** Long-term maintainability

| Task | File(s) | Effort |
|------|---------|--------|
| Create supporting Pydantic models | `models/*.py` | 2h |
| Standardize naming patterns | Various | 2h |
| Add field validators | `models/*.py` | 2h |
| Fix blueprint loading | `agents/*.py` | 2h |
| Implement BaseService | `services/base.py` (new) | 2h |
| Create Result type | `types.py` (new) | 1h |
| Add transaction management | `db/transaction.py` (new) | 1h |
| Complete mapper layer | `mappers/*.py` | 4h |

**Deliverable:** Clean mypy, consistent patterns, improved architecture

---

## File-by-File Fix Reference

### `roboco/db/tables.py`
- [ ] P0-001: Add `delivered_at` column to NotificationTable

### `roboco/models/base.py`
- [ ] P0-003: Add `ACCEPTED` to HandoffStatus enum

### `roboco/services/notification_delivery.py`
- [ ] P0-002: Fix `ack_read_at` to use correct field
- [ ] P1-001: Fix UUID conversions (lines 245, 253, 254)

### `roboco/services/task.py`
- [ ] P0-004: Fix `validate_task_ownership` parameter name (line 201)
- [ ] P1-001: Fix UUID conversions (lines 169, 237, 244)

### `roboco/agents/developer.py`
- [ ] P0-005: Implement `_initialize` and `_cleanup`
- [ ] P0-006: Fix `field(default=datetime)` (line 50)
- [ ] P1-004: Fix Any returns (line 596)

### `roboco/agents/documenter.py`
- [ ] P0-005: Implement `_initialize` and `_cleanup`
- [ ] P0-006: Fix `field(default=datetime)` (line 79)
- [ ] P1-004: Fix Any returns (lines 465, 474, 483, 492, 511)

### `roboco/agents/board.py`
- [ ] P0-005: Implement `_initialize` and `_cleanup`
- [ ] P0-006: Fix `field(default=datetime)` (lines 334, 346)
- [ ] P2-006: Fix timedelta.hours (line 530)

### `roboco/services/journal.py`
- [ ] P1-001: Fix UUID conversions (16 occurrences)
- [ ] P2-005: Refactor optimal dependency

### `roboco/services/kanban.py`
- [ ] P1-001: Fix UUID conversions (9 occurrences)
- [ ] P1-002: Fix Sequence vs list types (7 occurrences)

### `roboco/services/messaging.py`
- [ ] P1-001: Fix UUID conversions (10 occurrences)
- [ ] P2-001: Fix event bus private access

### `roboco/services/extraction.py`
- [ ] P0-006: Fix `field(default=datetime)` (line 152)
- [ ] P1-007: Fix content block type handling (line 411)
- [ ] P1-008: Fix ExtractedMessage/ExtractionResult fields (lines 422, 436)

### `roboco/runtime/orchestrator.py`
- [ ] P1-003: Add return type annotations (63 functions)
- [ ] P1-006: Fix indexed assignment on object (lines 647, 650)

### `roboco/mcp/task_server.py`
- [ ] P1-005: Fix FastMCP agent_id access (line 187)

### `roboco/mcp/message_server.py`
- [ ] P1-005: Fix FastMCP agent_id access (line 89)
- [ ] P1-004: Fix Any returns (lines 257, 264, 435, 444, 486, 495)

### `roboco/mcp/notify_server.py`
- [ ] P1-005: Fix FastMCP agent_id access (line 108)
- [ ] P1-004: Fix Any returns (lines 421, 461)

### `roboco/mcp/journal_server.py`
- [ ] P1-005: Fix FastMCP agent_id access (line 72)

### `roboco/llm/toon_adapter.py`
- [ ] P1-004: Fix Any returns (lines 73, 93, 103)

### `roboco/config.py`
- [ ] P1-006: Fix decorator order (lines 64, 73, 90, 123)

### `roboco/logging.py`
- [ ] P2-002: Fix processor type mismatch (line 48)

### `roboco/db/base.py`
- [ ] P2-003: Remove unreachable statement (line 129)

### `roboco/events/bus.py`
- [ ] P0-006: Fix `field(default=datetime)` (line 80)
- [ ] P2-001: Add `is_connected()` method

### `roboco/services/transcription.py`
- [ ] P0-006: Fix `field(default=datetime)` (lines 46, 47)

### `roboco/services/permissions.py`
- [ ] P1-004: Fix Any returns (lines 104, 110, 573)

### `roboco/services/optimal.py`
- [ ] P1-004: Fix Any returns (lines 237, 260)

### `roboco/services/metrics.py`
- [ ] P1-001: Fix UUID conversions (lines 273, 453)

---

## Testing Strategy

### Unit Tests Required
- [ ] UUID converter utility tests
- [ ] Mapper layer tests (ORM ↔ Pydantic)
- [ ] Field validator tests
- [ ] Service method tests with proper types

### Integration Tests Required
- [ ] Database migration tests
- [ ] Event bus connectivity tests
- [ ] MCP server tool tests

### Type Checking Verification
```bash
# Run after each sprint
uv run mypy roboco/ --ignore-missing-imports

# Target: 0 errors
```

---

## Appendix: Full Error Catalog

### Error Count by Category

| Category | Count |
|----------|-------|
| Missing return type annotation | 63 |
| UUID type incompatibility | 70+ |
| Field default with datetime | 9 |
| Abstract class instantiation | 9 |
| Returning Any | 25+ |
| Missing attribute | 6 |
| Sequence vs list | 7 |
| Decorator order | 4 |
| Other | 165 |
| **Total** | **358** |

### Error Count by File

| File | Errors |
|------|--------|
| runtime/orchestrator.py | 67 |
| services/journal.py | 16 |
| services/extraction.py | 12 |
| agents/documenter.py | 11 |
| services/messaging.py | 10 |
| agents/board.py | 9 |
| services/kanban.py | 9 |
| mcp/message_server.py | 6 |
| agents/developer.py | 5 |
| services/notification_delivery.py | 4 |
| services/task.py | 4 |
| Other (31 files) | 205 |
| **Total** | **358** |

---

## Conclusion

This plan provides a systematic approach to resolving all 358 type errors and architectural issues in the RoboCo codebase. Following the sprint plan will progressively improve type safety and code quality while minimizing risk of introducing regressions.

**Key Success Metrics:**
- Sprint 1: mypy errors < 300
- Sprint 2: mypy errors < 150
- Sprint 3: mypy errors < 50
- Sprint 4: mypy errors = 0

**Estimated Total Effort:** 8-13 developer days
