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
- **Branch requirement**: Branch auto-created on claim

## Releasing a Claimed Task

If you claimed a task but realize you shouldn't work on it, use `unclaim`:

```python
# Release back to pool
roboco_task_unclaim(task_id)

# Hand off to specific agent
roboco_task_unclaim(task_id, hand_off_to="be-dev-2")

# Result:
# - status: pending
# - assigned_to: None (or hand_off_to agent)
# - You can now claim new work
```

**When to use unclaim:**
- Task is out of your team's scope
- Task requires a different role
- You need to prioritize other work
- Better suited for another agent

**Restrictions:**
- Only works on `claimed` status (not yet started)
- You must be the agent who claimed it
- If task is `in_progress`, use `roboco_task_substitute` instead

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
