# A2A Collaboration Workflow

## Overview

Agents communicate directly via SDK Server (port 9000) for true peer-to-peer messaging.

**Key:** A2A requires `task_id` - it's about existing tasks, NOT task creation.

## Flow

```
1. Discover → roboco_agent_discover(role, team, skill)
2. Request  → roboco_agent_request(target, skill, message, task_id)
3. Check    → roboco_a2a_check() polls your inbox (auto-notified via hook)
4. Respond  → Work on task or reply via roboco_agent_request
```

## Delivery

| Target State | Delivery | Creates Notification? |
|--------------|----------|----------------------|
| Online | Direct HTTP to SDK | NO |
| Offline | Fallback via API | YES (spawns target) |

## Example

```python
# Request code review for task ABC123
result = roboco_agent_request(
    target_agent="be-qa",
    skill="code_review",
    message="Please review my changes",
    task_id="ABC123"
)
# result.delivery = "direct" or "notification"

# Check for incoming messages
inbox = roboco_a2a_check()
# inbox.messages = [{from, task_id, skill, message, priority}, ...]
```

## Urgency

```python
roboco_agent_request(..., options={"urgent": True})  # Priority queue
```

## Agent Skills

| Role | Skills |
|------|--------|
| Developer | `code_review`, `implementation`, `debugging`, `revision` |
| QA | `code_review`, `testing`, `qa_review` |
| Documenter | `documentation`, `api_docs` |
| PM | `task_planning`, `coordination`, `clarification` |

## Task Creation Rules

**Only PMs can create tasks.**

If you receive an A2A request that needs new work:
1. Escalate: `roboco_task_escalate(task_id, "Needs subtask for X")`
2. PM decides whether to create subtask

## Permissions

All agents can:
- Discover other agents
- Send A2A requests (must include task_id)
- Check request status

All agents CANNOT:
- Create tasks via A2A (no automatic task creation)
- Send A2A without a task_id
