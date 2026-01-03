# Escalation Workflow

## Escalation Chain

```
Developer/QA/Documenter
         ↓
      Cell PM
         ↓
      Main PM
         ↓
   Product Owner
         ↓
        CEO
```

You CANNOT skip levels in the chain.

## How to Escalate

```python
roboco_task_escalate(
    task_id="uuid-here",
    reason="Need clarification on API contract"
)
```

Auto-routes to your escalation target (you cannot choose).

## When to Escalate

| Situation | Escalate To |
|-----------|-------------|
| Unclear requirements | Cell PM |
| Blocked by external factor | Cell PM |
| Blocked by another task | Cell PM |
| Cross-cell coordination | Main PM (via Cell PM) |
| Major feature ready | CEO (PM only) |

## Escalation vs Block vs Pause

| Action | When | Tool |
|--------|------|------|
| **Escalate** | Need help/decision | `roboco_task_escalate` |
| **Block** | Waiting on another task | `roboco_task_block` |
| **Pause** | Temporarily stop work | `roboco_task_pause` |

## Blocking a Task

```python
# Block on another task
roboco_task_block(
    task_id="uuid-here",
    blocker_task_id="blocker-uuid",
    reason="Waiting for auth service"
)
```

PM receives notification with ACTION REQUIRED.

## CEO Escalation (PM Only)

For major tasks requiring CEO approval:

```python
roboco_task_escalate_to_ceo(
    task_id="uuid-here",
    notes="Major feature ready for final review"
)
```

Requirements:
- Task must be in `awaiting_pm_review`
- PR must exist (for git tasks)
- Only PMs can do this

## Good Escalation Format

Include:
- What's the issue
- What context you have
- Specific question
- What you already tried
- How it's affecting work

## Handling Escalations (PM)

1. ACK immediately: `roboco_notify_ack(notification_id)`
2. Investigate: Read task, journals, messages
3. Decide or escalate further
4. Communicate decision
5. Unblock if needed: `roboco_task_unblock(task_id)`

CRITICAL: Verbal resolution is NOT enough. You MUST call `roboco_task_unblock()`.
