# A2A (Agent-to-Agent) Tools

## Overview

A2A enables direct peer-to-peer communication between agents about existing tasks.

**Key points:**
- Direct HTTP when both agents online (no notification)
- Fallback to notification only when target offline
- All requests MUST reference an existing `task_id`

## Tools

### roboco_agent_discover

Find agents by role, team, or skill.

```python
roboco_agent_discover(
    role="developer",      # Optional: developer, qa, documenter, cell_pm, etc.
    team="backend",        # Optional: backend, frontend, ux_ui
    skill="code_review"    # Optional: specific capability
)
```

### roboco_agent_request

Send A2A message to another agent.

```python
roboco_agent_request(
    target_agent="be-qa",
    skill="code_review",
    message="Please review my changes",
    task_id="abc123...",              # REQUIRED
    options={"urgent": False}         # Optional: priority queue
)
```

**Returns:** `{status, delivery, message_id}` where `delivery` is `"direct"` or `"notification"`.

### roboco_a2a_check

Poll your inbox for incoming A2A messages.

```python
roboco_a2a_check()
```

**Returns:** `{messages: [...], count: N}` - messages from other agents.

**Note:** A hook automatically notifies you of pending messages after tool calls.

## Common Use Cases

| Need | Action |
|------|--------|
| Code review | `roboco_agent_request("be-qa", "code_review", "...", task_id)` |
| Clarification | `roboco_agent_request("be-pm", "clarification", "...", task_id)` |
| Find reviewer | `roboco_agent_discover(skill="code_review")` |
| Urgent help | `roboco_agent_request(..., options={"urgent": True})` |

## When to Use A2A

- Communication about an existing task you're working on
- Requesting code review, clarification, or help
- Notifying another agent about task progress
- Urgent questions needing immediate attention

## When NOT to Use A2A

- Creating new work → Only PMs create tasks via `roboco_task_create`
- Task assignments → PM assigns via `roboco_task_assign`
- Escalations → Use `roboco_task_escalate`
- Formal notifications → Use `roboco_notify_send` (PM only)

## Task Creation Rules

Only Cell PMs and Main PM can create tasks (subtasks).
If an agent receives an A2A request that requires new work:
1. Escalate to PM: `roboco_task_escalate(task_id, "Needs subtask for...")`
2. PM decides whether to create a subtask
