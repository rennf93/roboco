# Task Lifecycle Standards

Comprehensive standards for task management in the RoboCo system. These standards are derived from the actual implementation in the codebase.

**Source Files:**
- Task Status Enum: `roboco/models/base.py` (lines 19-34)
- Lifecycle Enforcement: `roboco/enforcement/task_lifecycle.py`
- Task Service: `roboco/services/task.py`

---

## Table of Contents

1. [Task States](#task-states)
2. [Valid Transitions](#valid-transitions)
3. [Role-Restricted Transitions](#role-restricted-transitions)
4. [Task Service Methods](#task-service-methods)
5. [Workflow by Role](#workflow-by-role)
6. [Quality Gates](#quality-gates)
7. [State Categories](#state-categories)

---

## Task States

### WF-001: TaskStatus Enum

**Source:** `roboco/models/base.py`

```python
class TaskStatus(str, Enum):
    """Task lifecycle states."""

    BACKLOG = "backlog"              # PM setup phase
    PENDING = "pending"              # Ready for work
    CLAIMED = "claimed"              # Agent has ownership
    IN_PROGRESS = "in_progress"      # Active work
    BLOCKED = "blocked"              # Waiting on dependency
    PAUSED = "paused"                # Temporarily stopped
    VERIFYING = "verifying"          # Self-verification
    NEEDS_REVISION = "needs_revision"# QA rejected
    AWAITING_QA = "awaiting_qa"      # Ready for QA
    AWAITING_DOCUMENTATION = "awaiting_documentation"  # QA passed
    AWAITING_PM_REVIEW = "awaiting_pm_review"          # Docs done
    COMPLETED = "completed"          # TERMINAL
    CANCELLED = "cancelled"          # TERMINAL
```

### WF-002: State Diagram

```
                              PM CREATES
                                  │
                                  ▼
                            ┌──────────┐
                            │ BACKLOG  │───► cancelled
                            └────┬─────┘
                                 │ activate()
                                 ▼
                            ┌──────────┐
                     ┌──────│ PENDING  │◄─────────────────────────────────┐
                     │      └────┬─────┘───► cancelled                    │
                     │           │                                        │
                     │       claim()                                      │
                     │           │                                        │
                     ▼           ▼                                        │
                            ┌──────────┐                                  │
                            │ CLAIMED  │───► pending, cancelled           │
                            └────┬─────┘                                  │
                                 │ start()                                │
                                 ▼                                        │
                         ┌─────────────┐                                  │
              ┌──────────│ IN_PROGRESS │───► completed, cancelled         │
              │          └──────┬──────┘                                  │
              │                 │                                         │
           block()           pause()                                      │
              │                 │                                         │
              ▼                 ▼                                         │
         ┌──────────┐      ┌─────────┐                                    │
         │ BLOCKED  │      │ PAUSED  │                                    │
         └────┬─────┘      └────┬────┘                                    │
              │                 │                                         │
           unblock()         resume()                                     │
              │                 │                                         │
              └────────►───────►└──────►──────┐                           │
                                              │                           │
                               submit_for_verification()                  │
                                              │                           │
                                              ▼                           │
                                       ┌───────────┐                      │
                                       │ VERIFYING │───► cancelled        │
                                       └─────┬─────┘                      │
                                             │                            │
                     ┌───────────────────────┼───────────────────────┐    │
                     │                       │                       │    │
            submit_for_qa()          needs_revision          direct to docs│
                     │                       │                       │    │
                     ▼                       ▼                       ▼    │
              ┌─────────────┐         ┌─────────────────┐  ┌─────────────────────┐
              │ AWAITING_QA │◄───────│ NEEDS_REVISION  │──│ AWAITING_DOCUMENTATION│
              └──────┬──────┘         └─────────────────┘  └──────────┬──────────┘
                     │                         ▲                      │
           ┌─────────┼─────────┐               │                      │
           │         │         │               │            docs_complete()
       pass_qa() fail_qa()   block            │                      │
           │         │         │               │                      ▼
           │         └─────────┴───────────────┘            ┌────────────────────┐
           │                                                │ AWAITING_PM_REVIEW │
           └────────────────────────────────────────────────└─────────┬──────────┘
                                                                      │
                                                               complete()
                                                                      │
                                                                      ▼
                                                                ┌───────────┐
                                                                │ COMPLETED │
                                                                └───────────┘
```

---

## Valid Transitions

### WF-010: Transition Matrix

**Source:** `roboco/enforcement/task_lifecycle.py` (lines 17-56)

| From Status | Valid Next States |
|-------------|-------------------|
| `backlog` | pending, cancelled |
| `pending` | claimed, cancelled |
| `claimed` | in_progress, pending, cancelled |
| `in_progress` | blocked, paused, verifying, completed, cancelled |
| `blocked` | in_progress, cancelled |
| `paused` | in_progress, cancelled |
| `verifying` | awaiting_qa, needs_revision, awaiting_documentation, cancelled |
| `needs_revision` | claimed, in_progress, cancelled |
| `awaiting_qa` | claimed, awaiting_documentation, needs_revision, blocked, cancelled |
| `awaiting_documentation` | claimed, awaiting_pm_review, cancelled |
| `awaiting_pm_review` | claimed, completed, cancelled |
| `completed` | *(TERMINAL - no transitions)* |
| `cancelled` | *(TERMINAL - no transitions)* |

### WF-011: Invalid Transitions

Any transition NOT in the matrix above will raise `InvalidTransitionError`.

```python
from roboco.enforcement.task_lifecycle import validate_task_transition

# This will raise InvalidTransitionError
validate_task_transition(
    TaskStatus.PENDING,
    TaskStatus.COMPLETED,  # Cannot skip the workflow!
    agent_role="developer"
)
```

---

## Role-Restricted Transitions

### WF-020: Permission Matrix

**Source:** `roboco/enforcement/task_lifecycle.py` (lines 66-93)

Certain transitions require specific roles:

| Transition | Allowed Roles |
|------------|---------------|
| `backlog → pending` | cell_pm, main_pm, product_owner, head_marketing |
| `awaiting_qa → claimed` | qa |
| `awaiting_qa → awaiting_documentation` | qa |
| `awaiting_qa → needs_revision` | qa |
| `awaiting_documentation → claimed` | documenter |
| `awaiting_documentation → awaiting_pm_review` | documenter |
| `awaiting_pm_review → claimed` | cell_pm, main_pm, product_owner, head_marketing |
| `awaiting_pm_review → completed` | cell_pm, main_pm, product_owner, head_marketing |
| `in_progress → completed` | cell_pm, main_pm, product_owner, head_marketing |
| `* → cancelled` | cell_pm, main_pm, product_owner, head_marketing |

### WF-021: Role Validation

```python
from roboco.enforcement.task_lifecycle import can_agent_transition

# Check if role can make transition
if can_agent_transition(
    current_status=TaskStatus.AWAITING_QA,
    new_status=TaskStatus.AWAITING_DOCUMENTATION,
    agent_role="developer"  # False - only QA can do this
):
    # proceed
```

---

## Task Service Methods

### WF-030: Status Change Methods

**Source:** `roboco/services/task.py`

| Method | Status Change | Calling Roles |
|--------|---------------|---------------|
| `activate()` | BACKLOG → PENDING | PM roles |
| `claim()` | → CLAIMED | developer, qa, documenter (based on current status) |
| `start()` | CLAIMED/PAUSED/NEEDS_REVISION → IN_PROGRESS | Owner |
| `block()` | IN_PROGRESS → BLOCKED | Owner |
| `soft_block()` | IN_PROGRESS → BLOCKED | Owner (external factor) |
| `unblock()` | BLOCKED → IN_PROGRESS | Owner or PM |
| `pause()` | IN_PROGRESS → PAUSED | Owner |
| `resume()` | PAUSED → IN_PROGRESS | Owner |
| `submit_for_verification()` | IN_PROGRESS → VERIFYING | Developer |
| `submit_for_qa()` | VERIFYING → AWAITING_QA | Developer |
| `pass_qa()` | AWAITING_QA → AWAITING_DOCUMENTATION | QA |
| `fail_qa()` | AWAITING_QA → NEEDS_REVISION | QA |
| `docs_complete()` | AWAITING_DOCUMENTATION → AWAITING_PM_REVIEW | Documenter |
| `submit_for_pm_review()` | IN_PROGRESS → AWAITING_PM_REVIEW | Any (non-dev tasks) |
| `complete()` | AWAITING_PM_REVIEW → COMPLETED | PM roles |
| `cancel()` | ANY → CANCELLED | PM roles |

### WF-031: Core Validation Method

All status changes go through `_validate_and_set_status()`:

```python
def _validate_and_set_status(
    self,
    task: TaskTable,
    new_status: TaskStatus,
    agent_role: str | None = None,
) -> None:
    """
    Validate and set task status with lifecycle enforcement.

    This is the single point of truth for status changes.
    """
```

---

## Workflow by Role

### WF-040: Developer Workflow

```
PENDING
    │ claim()
    ▼
CLAIMED
    │ start()
    ▼
IN_PROGRESS ←──────────────┐
    │                      │
    │ submit_for_          │ (from NEEDS_REVISION)
    │ verification()       │
    ▼                      │
VERIFYING                  │
    │                      │
    │ submit_for_qa()      │
    ▼                      │
AWAITING_QA ──fail_qa()──► NEEDS_REVISION
    │                           │
    │ pass_qa() (by QA)         │ claim() + start()
    ▼                           │
[QA/Docs workflow]              └───────────────────┘
```

**Developer can:**
- Claim `pending` and `needs_revision` tasks
- Start, pause, resume work
- Submit for verification and QA
- Block (with dependency)

**Developer cannot:**
- Pass/fail QA (that's QA's job)
- Complete tasks (that's PM's job)
- Cancel tasks

### WF-041: QA Workflow

```
AWAITING_QA
    │ claim()
    ▼
CLAIMED (QA owns)
    │ start()
    ▼
IN_PROGRESS
    │
    ├── pass_qa() ────► AWAITING_DOCUMENTATION
    │
    └── fail_qa() ────► NEEDS_REVISION (reassigned to developer)
```

**QA can:**
- Claim `awaiting_qa` tasks only
- Pass or fail QA
- Block tasks

**QA cannot:**
- Claim pending tasks (developers do that)
- Complete documentation
- Complete tasks

### WF-042: Documenter Workflow

```
AWAITING_DOCUMENTATION
    │ claim()
    ▼
CLAIMED (Documenter owns)
    │ start()
    ▼
IN_PROGRESS
    │ docs_complete()
    ▼
AWAITING_PM_REVIEW
```

**Documenter can:**
- Claim `awaiting_documentation` tasks
- Complete documentation

**Documenter cannot:**
- Claim pending tasks
- Pass/fail QA
- Complete tasks

### WF-043: PM Workflow

```
BACKLOG
    │ activate()
    ▼
PENDING
    │ (developers claim)
    ...
    ▼
AWAITING_PM_REVIEW
    │ complete()
    ▼
COMPLETED
```

**PM can:**
- Create tasks in backlog
- Activate backlog → pending
- Complete awaiting_pm_review tasks
- Cancel any task
- Unblock blocked tasks

---

## Quality Gates

### WF-050: Before Claiming

```markdown
- [ ] Task is in valid claim status for your role
- [ ] No existing in_progress task (one at a time)
- [ ] Dependencies are completed
```

### WF-051: Before Starting

```markdown
- [ ] Task is claimed by you
- [ ] Plan is documented
- [ ] Cell channel notified
```

### WF-052: Before Submit to QA

```markdown
- [ ] Tests passing: `uv run pytest`
- [ ] Linting clean: `uv run ruff check .`
- [ ] Type check: `uv run mypy roboco/`
- [ ] Self-review completed
- [ ] Journal reflection written
```

### WF-053: Before Completion

```markdown
- [ ] QA approved
- [ ] Documentation complete
- [ ] PM review approved
```

---

## State Categories

### WF-060: Helper Functions

**Source:** `roboco/enforcement/task_lifecycle.py`

| Function | Returns True For |
|----------|-----------------|
| `is_terminal_state()` | completed, cancelled |
| `is_waiting_state()` | blocked, paused, awaiting_qa, awaiting_documentation, awaiting_pm_review |
| `is_active_state()` | claimed, in_progress, verifying, needs_revision |

### WF-061: Terminal States

Once a task reaches `COMPLETED` or `CANCELLED`, no further transitions are possible.

```python
get_valid_transitions(TaskStatus.COMPLETED)  # Returns []
get_valid_transitions(TaskStatus.CANCELLED)  # Returns []
```

### WF-062: Waiting States

Tasks in waiting states are "on hold" pending some external action:

- `BLOCKED` - Waiting for blocker task
- `PAUSED` - Waiting for agent to resume
- `AWAITING_QA` - Waiting for QA review
- `AWAITING_DOCUMENTATION` - Waiting for docs
- `AWAITING_PM_REVIEW` - Waiting for PM approval

### WF-063: Active States

Tasks where an agent is actively working:

- `CLAIMED` - Agent owns, about to start
- `IN_PROGRESS` - Active development
- `VERIFYING` - Self-verification
- `NEEDS_REVISION` - Fixing QA issues

---

## Quick Reference

### Valid Claim Statuses by Role

| Role | Can Claim From |
|------|----------------|
| Developer | `pending`, `needs_revision` |
| QA | `awaiting_qa` |
| Documenter | `awaiting_documentation` |
| PM | `pending`, `backlog` |

### Common Status Flows

**Happy Path:**
```
backlog → pending → claimed → in_progress → verifying → awaiting_qa
→ awaiting_documentation → awaiting_pm_review → completed
```

**QA Rejection:**
```
awaiting_qa → needs_revision → claimed → in_progress → verifying → awaiting_qa
```

**Direct PM Review (non-dev task):**
```
pending → claimed → in_progress → awaiting_pm_review → completed
```

**Cancellation:**
```
any_state → cancelled
```
