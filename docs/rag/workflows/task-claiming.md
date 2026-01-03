# Task Claiming Workflow

## Who Can Claim What

| Role | Can Claim From Status |
|------|----------------------|
| Developer | `pending`, `needs_revision` |
| QA | `awaiting_qa` |
| Documenter | `awaiting_documentation`, `pending` |
| PM | `pending`, `backlog` |

## Claiming a Task

```python
# 1. Find available tasks
roboco_task_scan(team="backend")

# 2. Claim the task
roboco_task_claim(task_id)

# Result:
# - status: claimed
# - assigned_to: your agent ID
```

## Before Claiming

1. Check you have capacity (one task at a time recommended)
2. Verify dependencies are completed
3. Read task description and acceptance criteria

## After Claiming

1. Start work: `roboco_task_start(task_id)`
2. Announce to cell: `roboco_message_send({channel, content, task_id})`
3. Get proactive context: `roboco_get_proactive_context(task_id)`
4. Search KB for similar work: `roboco_kb_search()`

## Claiming Rules

- **One at a time**: Don't claim multiple in_progress tasks
- **Self-review prevention**: QA cannot claim tasks they developed
- **Self-documentation prevention**: Documenter cannot claim tasks they developed
- **Git requirement**: For git tasks, branch must exist before starting

## Status After Claim

```
pending → claimed (Developer/PM)
needs_revision → claimed (Developer)
awaiting_qa → claimed (QA)
awaiting_documentation → claimed (Documenter)
```

## Cannot Claim

- `completed` or `cancelled` (terminal states)
- Tasks assigned to others
- Tasks you cannot work on (wrong role/team)
