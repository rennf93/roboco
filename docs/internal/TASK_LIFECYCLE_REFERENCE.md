# RoboCo Task Lifecycle Reference

> Complete reference for task states, transitions, and lifecycle management.
> Source of truth: `roboco/enforcement/task_lifecycle.py`

---

## Table of Contents

1. [Task States Overview](#1-task-states-overview)
2. [State Machine Definition](#2-state-machine-definition)
3. [Transition Rules](#3-transition-rules)
4. [Role-Based Restrictions](#4-role-based-restrictions)
5. [Claiming Rules](#5-claiming-rules)
6. [Workflow Patterns](#6-workflow-patterns)
7. [Self-Review Prevention](#7-self-review-prevention)
8. [Special Operations](#8-special-operations)
9. [Code Reference](#9-code-reference)

---

## 1. Task States Overview

### All 13 States

| State | Description | Active? | Waiting? | Terminal? |
|-------|-------------|:-------:|:--------:|:---------:|
| BACKLOG | Created but not activated | - | - | - |
| PENDING | Ready for claiming | - | - | - |
| CLAIMED | Agent has taken ownership | ✓ | - | - |
| IN_PROGRESS | Active development | ✓ | - | - |
| BLOCKED | Waiting on dependency | - | ✓ | - |
| PAUSED | Agent paused work | - | ✓ | - |
| VERIFYING | Self-verification phase | ✓ | - | - |
| NEEDS_REVISION | Failed verification | ✓ | - | - |
| AWAITING_QA | In QA queue | - | ✓ | - |
| AWAITING_DOCUMENTATION | In docs queue | - | ✓ | - |
| AWAITING_PM_REVIEW | In PM approval queue | - | ✓ | - |
| COMPLETED | Successfully done | - | - | ✓ |
| CANCELLED | Cancelled | - | - | ✓ |

### State Categories

```python
# Active states - agent is actively working
ACTIVE_STATES = {
    TaskStatus.CLAIMED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.VERIFYING,
    TaskStatus.NEEDS_REVISION
}

# Waiting states - agent can work on other tasks
WAITING_STATES = {
    TaskStatus.BLOCKED,
    TaskStatus.PAUSED,
    TaskStatus.AWAITING_QA,
    TaskStatus.AWAITING_DOCUMENTATION,
    TaskStatus.AWAITING_PM_REVIEW
}

# Terminal states - no transitions out
TERMINAL_STATES = {
    TaskStatus.COMPLETED,
    TaskStatus.CANCELLED
}
```

---

## 2. State Machine Definition

### Complete Transition Graph

```
                                    ┌──────────────────────────────────────────────────────────────┐
                                    │                                                              │
                                    ▼                                                              │
┌─────────┐    PM Activates    ┌─────────┐   Agent Claims   ┌─────────┐   Starts Work   ┌─────────────┐
│ BACKLOG │ ─────────────────► │ PENDING │ ───────────────► │ CLAIMED │ ──────────────► │ IN_PROGRESS │
└─────────┘                    └─────────┘                  └─────────┘                 └─────────────┘
     │                              │                            │                            │
     │                              │                            │                            ├──► BLOCKED ──┐
     │                              │                            │                            │              │
     │                              │                            │                            ├──► PAUSED ───┤
     │                              │                            │                            │              │
     │                              │                            │                            ▼              │
     │                              │                            │                       ┌───────────┐       │
     │                              │                            │                       │ VERIFYING │       │
     │                              │                            │                       └───────────┘       │
     │                              │                            │                            │              │
     │                              │                            │         ┌──────────────────┼──────────────┘
     │                              │                            │         │                  │
     │                              │                            │         ▼                  ▼
     │                              │                            │  ┌──────────────┐   ┌─────────────┐
     │                              │                            │  │NEEDS_REVISION│   │ AWAITING_QA │
     │                              │                            │  └──────────────┘   └─────────────┘
     │                              │                            │         │                  │
     │                              │                            │         │                  ▼
     │                              │                            │         │          ┌───────────────────────┐
     │                              │                            └─────────┴─────────►│ AWAITING_DOCUMENTATION │
     │                              │                                                 └───────────────────────┘
     │                              │                                                          │
     │                              │                                                          ▼
     │                              │                                                 ┌──────────────────────┐
     │                              │                                                 │ AWAITING_PM_REVIEW   │
     │                              │                                                 └──────────────────────┘
     │                              │                                                          │
     │                              │                                                          ▼
     │                              │                                                    ┌───────────┐
     └──────────────────────────────┴────────────────────────────────────────────────────► COMPLETED │
                                    │                                                    └───────────┘
                                    │
                                    ▼
                               ┌───────────┐
                               │ CANCELLED │
                               └───────────┘
```

### Valid Transitions Table

| From | Valid To States |
|------|-----------------|
| BACKLOG | pending, cancelled |
| PENDING | claimed, cancelled |
| CLAIMED | in_progress, pending, cancelled |
| IN_PROGRESS | blocked, paused, verifying, awaiting_pm_review, completed, cancelled |
| BLOCKED | in_progress, cancelled |
| PAUSED | in_progress, cancelled |
| VERIFYING | awaiting_qa, needs_revision, awaiting_documentation, cancelled |
| NEEDS_REVISION | claimed, in_progress, cancelled |
| AWAITING_QA | claimed, awaiting_documentation, needs_revision, blocked, cancelled |
| AWAITING_DOCUMENTATION | claimed, awaiting_pm_review, cancelled |
| AWAITING_PM_REVIEW | claimed, completed, cancelled |
| COMPLETED | *(none - terminal)* |
| CANCELLED | *(none - terminal)* |

---

## 3. Transition Rules

### Standard Transitions

| Transition | Trigger | Notes |
|------------|---------|-------|
| BACKLOG → PENDING | PM activates | Requires linked session |
| PENDING → CLAIMED | Agent claims | Role-based claiming rules apply |
| CLAIMED → IN_PROGRESS | Agent starts | **Requires plan to be set** |
| CLAIMED → PENDING | Agent releases | Returns to pool |
| IN_PROGRESS → BLOCKED | Block reported | Hard (task dep) or soft (external) |
| IN_PROGRESS → PAUSED | Agent pauses | Can resume later |
| IN_PROGRESS → VERIFYING | Self-verify | Developer checks own work |
| IN_PROGRESS → AWAITING_PM_REVIEW | Direct submit | Skip QA/Docs path |
| IN_PROGRESS → COMPLETED | PM completes | PM-only shortcut |
| BLOCKED → IN_PROGRESS | Unblock | Resumes work |
| PAUSED → IN_PROGRESS | Resume | Continues work |
| VERIFYING → AWAITING_QA | Submit to QA | Normal path |
| VERIFYING → NEEDS_REVISION | Self-check fails | Developer fixes |
| VERIFYING → AWAITING_DOCUMENTATION | Skip QA | Alternative path |
| NEEDS_REVISION → CLAIMED | Re-claim | After fixing |
| NEEDS_REVISION → IN_PROGRESS | Continue work | Direct continuation |
| AWAITING_QA → AWAITING_DOCUMENTATION | QA passes | QA approval |
| AWAITING_QA → NEEDS_REVISION | QA fails | Back to developer |
| AWAITING_QA → BLOCKED | Issue found | External dependency |
| AWAITING_QA → CLAIMED | Reassign | PM reassigns |
| AWAITING_DOCUMENTATION → AWAITING_PM_REVIEW | Docs done | Documenter completes |
| AWAITING_DOCUMENTATION → CLAIMED | Reassign | PM reassigns |
| AWAITING_PM_REVIEW → COMPLETED | PM approves | **Final approval** |
| AWAITING_PM_REVIEW → CLAIMED | PM rejects | Back to developer |
| Any non-terminal → CANCELLED | PM cancels | PM-only action |

### Plan Requirement

```python
# CLAIMED → IN_PROGRESS requires task.plan to be set

if not task.plan:
    raise TaskLifecycleError(
        "Task must have a plan before starting work"
    )
```

---

## 4. Role-Based Restrictions

### Restricted Transitions

| From | To | Allowed Roles |
|------|-----|---------------|
| BACKLOG | PENDING | cell_pm, main_pm, product_owner, head_marketing |
| AWAITING_QA | CLAIMED | qa |
| AWAITING_QA | AWAITING_DOCUMENTATION | qa |
| AWAITING_QA | NEEDS_REVISION | qa |
| AWAITING_DOCUMENTATION | CLAIMED | documenter |
| AWAITING_DOCUMENTATION | AWAITING_PM_REVIEW | documenter |
| AWAITING_PM_REVIEW | CLAIMED | cell_pm, main_pm, product_owner, head_marketing |
| AWAITING_PM_REVIEW | COMPLETED | cell_pm, main_pm, product_owner, head_marketing |
| IN_PROGRESS | COMPLETED | cell_pm, main_pm, product_owner, head_marketing |
| IN_PROGRESS | AWAITING_PM_REVIEW | cell_pm, main_pm, product_owner, head_marketing, qa, documenter |
| * | CANCELLED | cell_pm, main_pm, product_owner, head_marketing |

### Special Role Rules

```python
# CEO and Auditor CANNOT cancel tasks
# They are observers only

CANCEL_ALLOWED_ROLES = {
    AgentRole.CELL_PM,
    AgentRole.MAIN_PM,
    AgentRole.PRODUCT_OWNER,
    AgentRole.HEAD_MARKETING
}
# Note: CEO and AUDITOR are intentionally excluded
```

---

## 5. Claiming Rules

### Claimable Status by Role

| Role | Can Claim From |
|------|----------------|
| DEVELOPER | pending, needs_revision |
| QA | pending, awaiting_qa |
| DOCUMENTER | pending, awaiting_documentation |
| CELL_PM | pending, awaiting_pm_review |
| MAIN_PM | pending, awaiting_pm_review (any team) |

### Claiming Validation Checks

Claim validation is layered across the MCP handler and the service:

```text
mcp.tasks.handlers._helpers.validate_task_claimable(task, role, agent_id, client)
    → role ↔ status matching (e.g. QA may only claim AWAITING_QA, etc.)

mcp.tasks.handlers.claim._check_active_tasks(client, exclude_task_id)
    → no other claimed/in_progress/verifying tasks for the agent

mcp.tasks.handlers.claim._validate_git_requirements(client, task, task_id)
    → project_id is set; parent task has a branch (for subtasks)

mcp.tasks.handlers.claim._validate_sibling_sequence(client, task)
    → earlier-sequence siblings must be completed/cancelled

services.task.TaskService.claim(task_id, agent_id, allow_reassign)
    → _validate_claim_status / _validate_claim_team / _validate_not_self_review
    → auto-create branch + work session
```

### Active Task Blocking

An agent **cannot claim a new task** if they have tasks in these statuses:
- CLAIMED
- IN_PROGRESS
- VERIFYING

An agent **cannot claim a new task** if they have tasks in these statuses:
- PAUSED (must resume first)

### Team Restrictions

```python
# Regular cell members must match task team
if agent_team != task_team and not is_management(agent_id):
    # Warning (not error) - cross-team claim allowed but flagged
    log.warning("Cross-team claim", agent=agent_id, task_team=task_team)
```

---

## 6. Workflow Patterns

### Standard Developer Workflow

```
PENDING
   │
   ├─► Agent claims task
   ▼
CLAIMED
   │
   ├─► Agent creates plan
   ├─► Agent starts work
   ▼
IN_PROGRESS
   │
   ├─► Development work
   ├─► Commits code
   ├─► Adds progress updates
   ▼
VERIFYING
   │
   ├─► Self-test
   ├─► Self-review
   ▼
AWAITING_QA
   │
   ├─► QA reviews
   ├─► QA approves
   ▼
AWAITING_DOCUMENTATION
   │
   ├─► Documenter writes docs
   ▼
AWAITING_PM_REVIEW
   │
   ├─► PM reviews
   ├─► PM approves
   ▼
COMPLETED
```

### QA Rejection Flow

```
AWAITING_QA
   │
   ├─► QA rejects (fail_qa)
   ▼
NEEDS_REVISION
   │
   ├─► Developer reclaims
   ▼
CLAIMED (or IN_PROGRESS)
   │
   ├─► Developer fixes issues
   ▼
VERIFYING
   │
   ├─► Re-submit to QA
   ▼
AWAITING_QA
   │
   └─► Repeat until pass
```

### Blocking Flow

```
IN_PROGRESS
   │
   ├─► Hard block (task dependency)
   │   └─► block(task_id, blocker_task_id)
   │
   ├─► Soft block (external factor)
   │   └─► soft_block(task_id, reason, type, what_needed)
   ▼
BLOCKED
   │
   ├─► Wait for resolution
   ├─► PM notified
   │
   ├─► Blocker resolved
   │   └─► unblock(task_id)
   ▼
IN_PROGRESS
```

### Direct PM Submission Flow

```
IN_PROGRESS
   │
   ├─► Agent submits directly to PM
   │   └─► submit_pm_review(task_id)
   ▼
AWAITING_PM_REVIEW
   │
   ├─► Skips QA and Documentation
   ├─► PM reviews directly
   ▼
COMPLETED
```

---

## 7. Self-Review Prevention

### The Rule

> QA cannot review tasks where they were the original developer.
> Documenter cannot document tasks where they were the original developer.

### Implementation

```python
# When task is submitted for QA, store original developer
task.quick_context = f"original_developer:{agent_id}"

# When QA/Documenter claims, check
def can_review_task(claiming_agent_id: str, task: TaskTable) -> bool:
    if task.quick_context and task.quick_context.startswith("original_developer:"):
        original_dev = task.quick_context.split(":")[1]
        if original_dev == claiming_agent_id:
            return False  # Cannot review own work
    return True
```

### Error Response

```python
TaskOwnershipError(
    agent_id=claiming_agent_id,
    task_id=task_id,
    action="claim",
    message="Cannot claim task for review - you were the original developer"
)
```

---

## 8. Special Operations

### Task Activation (BACKLOG → PENDING)

```python
# Requirements:
# 1. Task must be in BACKLOG
# 2. Task must have a linked session
# 3. Caller must be PM or Board member

async def activate(task_id: UUID) -> TaskTable:
    task = await self.get(task_id)
    if task.status != TaskStatus.BACKLOG:
        raise TaskLifecycleError("Task must be in BACKLOG to activate")
    # PM creates session, links it, then activates
    task.status = TaskStatus.PENDING
    return task
```

### Task Completion with Cancelled Children

```python
# CEO can force-complete a parent task even if some children are cancelled

async def complete(
    task_id: UUID,
    force_with_cancelled: bool = False,
    justification: str | None = None
) -> TaskTable:
    # Check descendants
    descendants = await self.get_all_descendants(task_id)

    for d in descendants:
        if d.status not in [TaskStatus.COMPLETED, TaskStatus.CANCELLED]:
            raise TaskLifecycleError("All descendants must be completed or cancelled")

    if any(d.status == TaskStatus.CANCELLED for d in descendants):
        if not force_with_cancelled:
            raise TaskLifecycleError(
                "Some descendants are cancelled. Use force_with_cancelled=True"
            )
        if not justification:
            raise ValidationError("Justification required for force completion")
        # CEO-level override
        task.dev_notes = f"{task.dev_notes}\n\nFORCE COMPLETED: {justification}"

    task.status = TaskStatus.COMPLETED
    task.completed_at = datetime.now(UTC)
    return task
```

### Task Escalation

```python
# Any agent can escalate (not permission-restricted)
# Follows escalation chain: Dev → Cell PM → Main PM → PO → CEO

async def escalate(task_id: UUID, agent_id: UUID, reason: str) -> TaskTable:
    agent_role = get_agent_role(agent_id)
    escalation_target = get_escalation_target(agent_id)

    # Create high-priority notification
    notification = create_blocker_escalation(
        from_agent=agent_id,
        to_agent=escalation_target,
        task_id=task_id,
        description=reason
    )

    return task
```

### Task Substitution

```python
# Agent requests to be released from task
# Reasons: low_context, out_of_scope_team, out_of_scope_role,
#          task_complete, max_retries, blocked_external

async def substitute(
    task_id: UUID,
    agent_id: UUID,
    reason: SubstituteReason
) -> TaskTable:
    # Map reason to new status
    STATUS_MAP = {
        SubstituteReason.TASK_COMPLETE: TaskStatus.AWAITING_QA,  # or AWAITING_PM_REVIEW
        SubstituteReason.LOW_CONTEXT: TaskStatus.PENDING,
        SubstituteReason.OUT_OF_SCOPE_TEAM: TaskStatus.PENDING,
        SubstituteReason.OUT_OF_SCOPE_ROLE: TaskStatus.PENDING,
        SubstituteReason.MAX_RETRIES: TaskStatus.PENDING,
        SubstituteReason.BLOCKED_EXTERNAL: TaskStatus.BLOCKED
    }

    new_status = STATUS_MAP[reason]
    task.status = new_status
    task.assigned_to = None  # Release assignment

    # Notify PM
    notify_pm_of_substitution(task, agent_id, reason)

    return task
```

---

## 9. Code Reference

### Key Files

| File | Purpose |
|------|---------|
| `roboco/enforcement/task_lifecycle.py` | State machine definition, transition validation |
| `roboco/enforcement/task_ownership.py` | Ownership and claiming rules |
| `roboco/services/task.py` | Task service with all operations |
| `roboco/api/routes/tasks.py` | REST API endpoints |
| `roboco/mcp/tasks/handlers/` | MCP tool handlers |

### Core Functions

```python
# In task_lifecycle.py

def validate_task_transition(
    current_status: str,
    target_status: str,
    agent_role: str | None = None
) -> None:
    """
    Raises TaskLifecycleError if:
    - Transition is not in VALID_TRANSITIONS
    - Role is not in ROLE_RESTRICTED_TRANSITIONS (if applicable)
    """

def can_agent_transition(
    current_status: str,
    target_status: str,
    role: str
) -> bool:
    """Non-raising version - returns True/False"""

def get_valid_transitions(status: str) -> list[str]:
    """Returns list of valid target statuses"""

def is_terminal_state(status: str) -> bool:
    """Returns True for COMPLETED or CANCELLED"""

def is_active_state(status: str) -> bool:
    """Returns True if agent should be actively working"""

def is_waiting_state(status: str) -> bool:
    """Returns True if agent can work on other tasks"""
```

### TaskService Methods

```python
# In services/task.py

# Core lifecycle
async def claim(task_id, agent_id, allow_reassign=False)
async def start(task_id, agent_id=None)
async def pause(task_id)
async def resume(task_id)
async def block(task_id, blocker_task_id)
async def soft_block(task_id, reason, blocker_type, what_needed)
async def unblock(task_id)
async def submit_for_verification(task_id)
async def submit_for_qa(task_id)
async def pass_qa(task_id, notes=None)
async def fail_qa(task_id, notes)  # notes required
async def docs_complete(task_id, doc_notes=None)
async def submit_for_pm_review(task_id, notes=None)
async def complete(task_id, agent_id=None, force_with_cancelled=False, justification=None)
async def cancel(task_id, agent_role="cell_pm")
async def activate(task_id)

# Special operations
async def escalate(task_id, agent_id, reason)
async def substitute(task_id, agent_id, reason: SubstituteReason)
```

### API Endpoints

```python
# Lifecycle endpoints in routes/tasks.py

POST /{task_id}/claim
POST /{task_id}/start
POST /{task_id}/block
POST /{task_id}/soft-block
POST /{task_id}/unblock
POST /{task_id}/pause
POST /{task_id}/resume
POST /{task_id}/verify
POST /{task_id}/submit-qa
POST /{task_id}/pass-qa
POST /{task_id}/fail-qa
POST /{task_id}/docs-complete
POST /{task_id}/submit-pm-review
POST /{task_id}/complete
POST /{task_id}/cancel
POST /{task_id}/activate
POST /{task_id}/escalate
POST /{task_id}/substitute
```

---

## Quick Reference Card

### Status Colors

| Status | Color | Meaning |
|--------|-------|---------|
| BACKLOG | Gray | Not yet activated |
| PENDING | Blue | Ready to claim |
| CLAIMED | Yellow | Agent owns it |
| IN_PROGRESS | Orange | Actively working |
| BLOCKED | Red | Waiting on blocker |
| PAUSED | Purple | Temporarily stopped |
| VERIFYING | Cyan | Self-checking |
| NEEDS_REVISION | Pink | Fix required |
| AWAITING_QA | Lime | QA queue |
| AWAITING_DOCUMENTATION | Teal | Docs queue |
| AWAITING_PM_REVIEW | Navy | PM approval |
| COMPLETED | Green | Done! |
| CANCELLED | Dark Gray | Cancelled |

### Common Errors

| Error | Cause | Resolution |
|-------|-------|------------|
| "Invalid transition" | State machine violation | Check VALID_TRANSITIONS |
| "Role not allowed" | Role restriction | Check ROLE_RESTRICTED_TRANSITIONS |
| "Task must have plan" | Missing plan | Create plan before starting |
| "Cannot claim - active tasks" | Already working | Complete or pause current task |
| "Cannot review own work" | Self-review attempt | Assign to different agent |
| "All descendants must be completed" | Parent completion | Complete all child tasks first |

---

*This document reflects the actual implementation in `roboco/enforcement/task_lifecycle.py` and `roboco/services/task.py` as of December 29, 2025.*
