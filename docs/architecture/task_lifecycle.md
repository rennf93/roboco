# Task Lifecycle

This document describes all task states, valid transitions, and workflow enforcement in the RoboCo system.

## Task States

Tasks follow a defined lifecycle from creation to completion. Each state represents a specific phase of work.

### State Definitions

| State | Description |
|-------|-------------|
| `backlog` | PM setup phase - task with dependencies or needs session setup |
| `pending` | Ready for work - orchestrator can spawn agents |
| `claimed` | Agent has taken ownership but not started |
| `in_progress` | Active work being performed |
| `blocked` | Waiting on another task or external dependency |
| `paused` | Temporarily suspended by the assigned agent |
| `verifying` | Self-verification by the developer |
| `needs_revision` | QA or CEO has requested changes |
| `awaiting_qa` | Ready for QA review |
| `awaiting_documentation` | Docs + Developer PR creation in parallel |
| `awaiting_pm_review` | After docs + PR ready, PM reviews |
| `awaiting_ceo_approval` | PMs approved, CEO makes final decision |
| `completed` | Task finished successfully (terminal) |
| `cancelled` | Task cancelled (terminal) |
| `quarantined` | Special state for problematic tasks |

### State Categories

```python
# Terminal states - cannot transition out
TERMINAL_STATES = ["completed", "cancelled"]

# Waiting states - agent can work on other tasks
WAITING_STATES = [
    "blocked",
    "paused",
    "awaiting_qa",
    "awaiting_documentation",
    "awaiting_pm_review",
    "awaiting_ceo_approval",
]

# Active states - agent is actively working
ACTIVE_STATES = ["claimed", "in_progress", "verifying", "needs_revision"]
```

## State Transition Diagram

```
                                    +-----------+
                                    | quarantined|
                                    +-----+-----+
                                          |
                                          v
+--------+     +----------+     +---------+     +-------------+
| backlog| --> | pending  | --> | claimed | --> | in_progress |
+---+----+     +----+-----+     +----+----+     +------+------+
    |               |                |                 |
    v               v                v                 |
+---------+    +---------+     +-----------+           |
|cancelled|    |cancelled|     | pending   |           |
+---------+    +---------+     |(unclaim)  |           |
                               +-----------+           |
                                                       |
                   +-----------------------------------+
                   |                |              |   |
                   v                v              v   v
            +----------+     +----------+    +---------+
            | blocked  |     | paused   |    |verifying|
            +----+-----+     +----+-----+    +----+----+
                 |                |               |
                 v                v               |
            in_progress     in_progress          |
                                                 |
                   +-----------------------------+
                   |                |            |
                   v                v            v
            +-------------+  +---------------+  +---------------------+
            | awaiting_qa |  |needs_revision |  |awaiting_documentation|
            +------+------+  +-------+-------+  +----------+----------+
                   |                 |                     |
                   |                 v                     |
                   |           claimed/in_progress         |
                   |                                       |
                   +-----------------+---------------------+
                                     |
                                     v
                            +------------------+
                            |awaiting_pm_review|
                            +--------+---------+
                                     |
           +-------------------------+-------------------------+
           |                         |                         |
           v                         v                         v
    +-----------+          +--------------------+        +-----------+
    | completed |          |awaiting_ceo_approval|       | cancelled |
    +-----------+          +---------+----------+        +-----------+
                                     |
           +-------------------------+-------------------------+
           |                         |                         |
           v                         v                         v
    +-----------+            +---------------+           +-----------+
    | completed |            | needs_revision|           | cancelled |
    +-----------+            +---------------+           +-----------+
```

## Valid Transitions

The following table shows all valid state transitions:

| From State | To States |
|------------|-----------|
| `backlog` | `pending`, `cancelled` |
| `pending` | `claimed`, `cancelled` |
| `claimed` | `in_progress`, `pending`, `cancelled` |
| `in_progress` | `blocked`, `paused`, `verifying`, `awaiting_pm_review`, `awaiting_documentation`, `needs_revision`, `completed`, `cancelled` |
| `blocked` | `in_progress`, `cancelled` |
| `paused` | `in_progress`, `cancelled` |
| `verifying` | `awaiting_qa`, `needs_revision`, `awaiting_documentation`, `cancelled` |
| `needs_revision` | `claimed`, `in_progress`, `cancelled` |
| `awaiting_qa` | `claimed`, `awaiting_documentation`, `needs_revision`, `blocked`, `cancelled` |
| `awaiting_documentation` | `claimed`, `awaiting_pm_review`, `cancelled` |
| `awaiting_pm_review` | `claimed`, `awaiting_ceo_approval`, `completed`, `cancelled` |
| `awaiting_ceo_approval` | `completed`, `needs_revision`, `cancelled` |
| `completed` | (none - terminal) |
| `cancelled` | (none - terminal) |
| `quarantined` | `pending` |

## Role-Based Restrictions

Certain transitions require specific roles:

### PM-Only Transitions
- `backlog` -> `pending` (activate task)
- `awaiting_pm_review` -> `completed`
- `awaiting_pm_review` -> `awaiting_ceo_approval`
- `in_progress` -> `completed` (PM completing their own task)
- All cancellation transitions

### QA-Only Transitions
- `awaiting_qa` -> `claimed` (QA claims)
- `awaiting_qa` -> `awaiting_documentation` (QA pass)
- `awaiting_qa` -> `needs_revision` (QA fail)
- `in_progress` -> `awaiting_documentation` (direct QA assignment pass)
- `in_progress` -> `needs_revision` (direct QA assignment fail)

### Documenter-Only Transitions
- `awaiting_documentation` -> `claimed` (Documenter claims)
- `awaiting_documentation` -> `awaiting_pm_review` (Docs complete)

### CEO-Only Transitions
- `awaiting_ceo_approval` -> `completed` (CEO approves)
- `awaiting_ceo_approval` -> `needs_revision` (CEO requests changes)
- `awaiting_ceo_approval` -> `cancelled` (CEO cancels)

### PM Cancel Roles
The following roles can cancel tasks:
- `cell_pm`
- `main_pm`
- `product_owner`
- `head_marketing`

## Git Integration

Tasks with `requires_git=True` have additional requirements:

### Git Workflow Requirements

1. **Starting Work** (`claimed` -> `in_progress`):
   - Task must have `branch_name` set (PM created the branch)

2. **Documentation to PM Review** (`awaiting_documentation` -> `awaiting_pm_review`):
   - Requires BOTH `docs_complete=True` AND `pr_created=True`
   - Documenter and Developer work in parallel during this phase

3. **PM Review to CEO Approval** (`awaiting_pm_review` -> `awaiting_ceo_approval`):
   - Task must have `pr_number` set (PR exists)

4. **CEO Approval to Completed** (`awaiting_ceo_approval` -> `completed`):
   - PR should be merged (CEO merges as final action)

### Parallel Execution Phase

During `awaiting_documentation`:
- **Documenter**: Works on docs, calls `roboco_task_docs_complete()` when done
- **Developer**: Creates PR, calls `roboco_git_create_pr()` when ready

Both must complete before transitioning to `awaiting_pm_review`.

```python
def check_parallel_completion(docs_complete: bool, pr_created: bool, requires_git: bool = True) -> bool:
    """Check if parallel execution is complete."""
    if not requires_git:
        return docs_complete
    return docs_complete and pr_created
```

## Task Types

Task types determine whether git workflow applies:

| Type | Git Required | Description |
|------|-------------|-------------|
| `code` | Yes | Technical work - full git workflow |
| `documentation` | Optional | May or may not need git |
| `research` | No | Investigation/analysis tasks |
| `planning` | No | Planning and design tasks |
| `design` | No | UX/UI design tasks |
| `administrative` | No | Administrative tasks |

## Workflow Enforcement

The `task_lifecycle.py` module enforces these rules:

```python
from roboco.enforcement.task_lifecycle import (
    validate_task_transition,
    validate_git_requirements,
    can_agent_transition,
    is_terminal_state,
    is_waiting_state,
    is_active_state,
)

# Validate a transition
validate_task_transition(
    current_status="in_progress",
    target_status="verifying",
    agent_role="developer"
)

# Check git requirements
from roboco.enforcement.task_lifecycle import GitContext

git_ctx = GitContext(
    requires_git=True,
    docs_complete=True,
    pr_created=True,
    pr_number=42,
    branch_name="feature/backend/ABC123"
)

validate_git_requirements(
    current_status="awaiting_documentation",
    target_status="awaiting_pm_review",
    git_ctx=git_ctx
)
```

## Exceptions

The lifecycle module raises specific exceptions:

- `TaskLifecycleError`: Invalid state transition or role not permitted
- `GitRequirementError`: Git requirements not met for a transition

Example error handling:

```python
from roboco.exceptions import TaskLifecycleError
from roboco.enforcement.task_lifecycle import GitRequirementError

try:
    validate_task_transition("pending", "in_progress", "developer")
except TaskLifecycleError as e:
    print(f"Invalid transition: {e.current_status} -> {e.target_status}")
    print(f"Valid transitions: {e.valid_transitions}")

try:
    validate_git_requirements("claimed", "in_progress", git_ctx)
except GitRequirementError as e:
    print(f"Git requirement not met: {e.requirement}")
    print(f"Message: {e.message}")
```

## Related API Endpoints

Task lifecycle operations are exposed via the Task API:

| Endpoint | Description |
|----------|-------------|
| `POST /tasks/{id}/claim` | Claim a pending task |
| `POST /tasks/{id}/start` | Start working on claimed task |
| `POST /tasks/{id}/block` | Block task on dependency |
| `POST /tasks/{id}/soft-block` | Block on external factor |
| `POST /tasks/{id}/unblock` | Unblock a task |
| `POST /tasks/{id}/pause` | Pause active task |
| `POST /tasks/{id}/resume` | Resume paused task |
| `POST /tasks/{id}/verify` | Submit for self-verification |
| `POST /tasks/{id}/submit-qa` | Submit to QA |
| `POST /tasks/{id}/pass-qa` | QA passes task |
| `POST /tasks/{id}/fail-qa` | QA fails task |
| `POST /tasks/{id}/docs-complete` | Mark docs complete |
| `POST /tasks/{id}/submit-pm-review` | Submit for PM review |
| `POST /tasks/{id}/escalate-to-ceo` | Escalate to CEO |
| `POST /tasks/{id}/ceo-approve` | CEO approves |
| `POST /tasks/{id}/ceo-reject` | CEO rejects |
| `POST /tasks/{id}/complete` | Complete task (PM) |
| `POST /tasks/{id}/cancel` | Cancel task (PM) |
| `POST /tasks/{id}/activate` | Activate from backlog (PM) |
