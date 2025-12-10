# RoboCo Data Models

This directory contains all Pydantic data models for the RoboCo AI Agents Company system.

## Model Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DATA MODEL HIERARCHY                                  │
└─────────────────────────────────────────────────────────────────────────────┘

Organization Layer:
├─► Agent          → Individual AI agents with roles, teams, permissions
├─► Channel        → Top-level communication containers (#backend-cell, etc.)
└─► Group          → Role-based groups within channels

Communication Layer:
├─► Session        → Bounded message groups (by time, count, or length)
├─► Message        → Extracted messages from agent streams
└─► Notification   → Formal signals requiring acknowledgment

Work Layer:
├─► Task           → Atomic unit of work with lifecycle states
├─► Journal        → Agent personal logs and reflections
└─► Handoff        → Dev → Documenter transition documents
```

## Files

| File | Description |
|------|-------------|
| `base.py` | Enums, base model class, common types |
| `task.py` | Task model with full lifecycle |
| `agent.py` | Agent model with roles and permissions |
| `session.py` | Session boundaries and management |
| `message.py` | Extracted messages and raw streams |
| `group.py` | Group model for role-based access |
| `channel.py` | Channel model for team structure |
| `notification.py` | Formal notification system |
| `journal.py` | Agent journaling and reflection |
| `handoff.py` | Documentation handoff system |

## Enums Reference

### TaskStatus
```
PENDING → CLAIMED → IN_PROGRESS → VERIFYING → AWAITING_QA → AWAITING_DOCUMENTATION → COMPLETED
                         ↓                         ↓
                      BLOCKED                NEEDS_REVISION
                         ↓
                       PAUSED
```

### AgentRole
```
CEO                 → Executive (Human)
PRODUCT_OWNER       → Board
HEAD_MARKETING      → Board
AUDITOR             → Board (Silent observer)
MAIN_PM             → Management
CELL_PM             → Cell management
DEVELOPER           → Cell member
QA                  → Cell member
DOCUMENTER          → Cell member
```

### Team
```
BACKEND   → Backend cell
FRONTEND  → Frontend cell
UX_UI     → UX/UI cell
BOARD     → Board level (no cell)
```

### MessageType
```
REASONING  → Agent's thought process
DIALOGUE   → Agent-to-agent conversation
DECISION   → Choice made during work
ACTION     → Observable work progress
BLOCKER    → Impediment identified
TECHNICAL  → Code explanations
```

### NotificationType
```
TASK_ASSIGNMENT       → New task assigned
PRIORITY_CHANGE       → Task priority changed
BLOCKER_ESCALATION    → Blocker needs resolution
REVIEW_REQUEST        → Ready for QA
DOCUMENTATION_REQUEST → Ready for docs
ALERT                 → Urgent attention needed
BROADCAST             → Company-wide announcement
```

## Usage Examples

### Creating a Task
```python
from roboco.models import Task, TaskCreate, Team, Complexity
from uuid import uuid4

# Create via schema
task_data = TaskCreate(
    title="Implement rate limiting",
    description="Add rate limiting to auth endpoints",
    acceptance_criteria=[
        "Rate limit of 5 attempts per minute",
        "Return 429 on limit exceeded",
        "Use Redis for distributed counting",
    ],
    team=Team.BACKEND,
    priority=1,
    estimated_complexity=Complexity.MEDIUM,
)

# Create full task
task = Task(
    **task_data.model_dump(),
    created_by=uuid4(),
)

# Use lifecycle methods
task.claim(agent_id=uuid4())
task.start()
task.add_progress(agent_id=task.assigned_to, message="Working on Redis integration", percentage=25)
task.add_commit(hash="abc1234", message="feat(auth): add rate limiting", agent_id=task.assigned_to)
```

### Creating an Agent
```python
from roboco.models import Agent, AgentCreate, AgentRole, Team, ModelConfig

agent = Agent(
    name="Backend Developer 1",
    slug="be-dev-1",
    role=AgentRole.DEVELOPER,
    team=Team.BACKEND,
    model=ModelConfig(
        provider="anthropic",
        name="claude-3-opus",
        fallback="local-llama-70b",
    ),
    system_prompt="You are a senior backend developer...",
    capabilities=["code_execution", "git_operations", "file_management"],
)

agent.go_online()
agent.assign_task(task_id=task.id)
```

### Creating a Notification
```python
from roboco.models.notification import create_task_assignment, NotificationPriority

notification = create_task_assignment(
    from_pm=pm_id,
    to_agent=developer_id,
    task_id=task.id,
    task_title=task.title,
    priority=NotificationPriority.HIGH,
)

# Recipients acknowledge
notification.acknowledge(developer_id)
```

### Creating a Journal Entry
```python
from roboco.models.journal import create_task_reflection

entry = create_task_reflection(
    journal_id=agent.journal_id,
    task_id=task.id,
    title="Rate Limiting Implementation",
    what_done="Implemented sliding window rate limiting with Redis",
    what_learned="Redis MULTI/EXEC is essential for atomic operations",
    what_struggled="Getting the window calculation right",
    next_steps=["Add configuration options", "Write integration tests"],
    tags=["redis", "rate-limiting", "auth"],
)
```

## Validation

All models use Pydantic v2 with strict validation:

- Type checking enforced
- Field constraints validated
- Extra fields forbidden
- Enum values used in serialization

## Extending Models

When adding new models:

1. Create in appropriate file or new file
2. Inherit from `RobocoBase` or `TimestampMixin`
3. Add Create/Update schemas for API use
4. Export in `__init__.py`
5. Add factory functions for common patterns

## Database Considerations

These are Pydantic models for validation and serialization. For database persistence:

- PostgreSQL via SQLAlchemy (to be implemented)
- Redis for sessions and caching
- Qdrant for embeddings (vector fields)

The `embedding` fields are `list[float]` in Pydantic but will map to vector types in the database.
