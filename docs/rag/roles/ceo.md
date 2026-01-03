# CEO Role

## Identity

- **Agent**: ceo (Renzo - Human)
- **Role**: `ceo`
- **Team**: executive
- **Reports to**: N/A (top of hierarchy)

## Core Responsibilities

1. Final authority on major decisions
2. Approve major task completions
3. Set strategic direction
4. Oversee entire organization

## What You CAN Do

- View ALL tasks organization-wide
- Approve/reject tasks in `awaiting_ceo_approval`
- Force complete tasks with cancelled subtasks
- Send notifications to anyone
- Full access to all channels

## What You CANNOT Do

- Cancel tasks (by design - CEO observes/approves, doesn't manage)
- Should not be doing day-to-day task management

## CEO Approval Workflow

When PM escalates major task:

```python
# Task arrives in awaiting_ceo_approval
# CEO reviews and decides:

# Approve and complete
roboco_task_ceo_approve(task_id, notes="Approved. Great work!")

# Reject and send back
roboco_task_ceo_reject(task_id, notes="Need to address X before merge")
```

## Force Completion

When subtasks are cancelled but parent should complete:

```python
roboco_task_complete(
    task_id,
    force_with_cancelled=True,
    justification="Subtask no longer needed"
)
```

Only CEO can use `force_with_cancelled`.

## Escalation

CEO is the final escalation target. Issues escalate:
```
Developer → Cell PM → Main PM → Product Owner → CEO
```

## Communication

CEO has access to all channels including:
- #board-private
- #announcements (write)
- All cell and cross-cell channels
