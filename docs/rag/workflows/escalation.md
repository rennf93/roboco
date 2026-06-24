# Escalation Workflow

## Escalation Chain

```
Developer/QA/Documenter
         ↓
      Cell PM
         ↓
      Main PM
         ↓
   Product Owner / Head of Marketing (Board)
         ↓
        CEO
```

`escalate_up` walks this chain **one rung at a time** — it auto-routes to your immediate escalation target; you cannot choose a higher level or skip a rung.

The one exception is `escalate_to_ceo`: it is a **separate** verb, available only to Main PM and the Board (Product Owner / Head of Marketing), that goes straight to the CEO for final approval of a major task. It is not part of the `escalate_up` chain.

## How to Escalate (up one rung)

```python
escalate_up(
    task_id="<task>",
    reason="Need clarification on the API contract",
)
```

Auto-routes to your escalation target (you cannot choose it).

## When to Escalate

| Situation | Escalate To |
|-----------|-------------|
| Unclear requirements | Cell PM |
| Blocked by external factor | Cell PM |
| Blocked by another task | Cell PM |
| Cross-cell coordination | Main PM (via Cell PM) |
| Major feature ready for CEO sign-off | CEO (via `escalate_to_ceo`, PM/Board only) |

## Escalate vs Block

| Action | When | Verb |
|--------|------|------|
| **Escalate** | Need a decision / help from above | `escalate_up` |
| **Block** | Can't proceed on an external dependency | `i_am_blocked` |

There is no agent-facing "pause" verb. If you need to step off a task you claimed but haven't progressed, use `unclaim(task_id)` to return it to the pool.

## Blocking a Task

```python
i_am_blocked(
    task_id="<task>",
    reason="Waiting for the auth service to land",
    blocker_type="external",
    what_needed="auth-service /token endpoint deployed",
)
```

Your Cell PM is notified and is the one who can `unblock` it.

## CEO Escalation (Main PM / Board Only)

For major tasks requiring CEO approval:

```python
escalate_to_ceo(
    task_id="<task>",
    reason="Major feature ready for final review",
)
```

Requirements:
- Task must be in `awaiting_pm_review`
- PR must exist
- Only Main PM, Product Owner, or Head of Marketing can call it
- **PARENT TASKS ONLY** — subtasks cannot be escalated to CEO

If you need to escalate a subtask, escalate the parent task instead. The CEO reviews the complete feature, not individual components.

## Good Escalation Format

Include:
- What's the issue
- What context you have
- Specific question
- What you already tried
- How it's affecting work

## Handling Escalations (PM)

1. ACK the notification: `notify_ack(notification_id)`
2. Investigate: read the task, journals, and channel messages
3. Decide, or escalate further with `escalate_up`
4. Communicate the decision (`say` / `dm` / `notify`)
5. Unblock if needed: `unblock(task_id, reason)`

CRITICAL: Verbal resolution is NOT enough. To clear a block you MUST call `unblock(task_id, reason)`. The `reason` (why you are clearing the block) is recorded as your `journal:decision` — no separate `note(scope='decision')` call is required.
