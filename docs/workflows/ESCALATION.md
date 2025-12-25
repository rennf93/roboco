# Escalation Guide

## Escalation Chain

```
Developer/QA/Documenter
         │
         ▼
      Cell PM
         │
         ▼
      Main PM
         │
         ▼
   Product Owner
         │
         ▼
        CEO
```

---

## When to Escalate

| Situation | Escalate To | Tool |
|-----------|-------------|------|
| Need PM decision | Cell PM | `roboco_task_escalate` |
| Blocked by external factor | Cell PM | `roboco_task_escalate` |
| Cross-cell coordination needed | Cell PM → Main PM | `roboco_task_escalate` |
| Scope creep beyond task | Cell PM | `roboco_task_escalate` |
| Resource/priority conflict | Cell PM | `roboco_task_escalate` |
| Cell PM unresponsive | Main PM | `roboco_task_escalate` |
| Company-wide issue | Product Owner | `roboco_escalate` (PM only) |

---

## Escalation Tools

### For All Agents: `roboco_task_escalate`

Escalate a task-related issue:

```python
roboco_task_escalate(
    task_id="uuid-here",
    reason="Need clarification on API contract - acceptance criteria unclear",
    escalate_to="be-pm"         # Optional - auto-routes if omitted
)
```

**Auto-routing (when `escalate_to` omitted):**
- Developer/QA/Doc → Cell PM
- Cell PM → Main PM
- Main PM → Product Owner

### For PM/Board Only: `roboco_escalate`

General escalation (not task-specific):

```python
roboco_escalate(
    escalate_to="main-pm",
    subject="Need cross-cell coordination",
    description="Backend and frontend teams need to sync on API changes",
    task_id="uuid-optional"     # Optional link
)
```

---

## Escalation Reasons by Role

### Developer Escalations

| Reason | Example |
|--------|---------|
| Unclear requirements | "Acceptance criteria doesn't specify error handling" |
| Blocked by other task | "Waiting on auth service from fe-dev-1" |
| Scope question | "Should I also handle edge case X?" |
| Need decision | "Two valid approaches - need PM guidance" |
| Technical blocker | "Can't reproduce bug in dev environment" |

### QA Escalations

| Reason | Example |
|--------|---------|
| Can't reproduce | "Bug not reproducible with given steps" |
| Unclear test criteria | "Don't know what 'acceptable performance' means" |
| Blocking issue found | "Critical security flaw - should we halt?" |
| Test environment issue | "Staging is down, can't proceed" |

### Cell PM Escalations

| Reason | Example |
|--------|---------|
| Cross-cell dependency | "Need frontend to expose new endpoint" |
| Resource conflict | "Both tasks need be-dev-1, can't parallelize" |
| Priority question | "Two P1 tasks - which first?" |
| Scope change | "Requirements changed mid-sprint" |

---

## What Happens When You Escalate

1. **Escalation notification sent** to target
2. **Task status unchanged** (you can keep working if possible)
3. **Escalation logged** in task history
4. **Target must ACK** the escalation
5. **Resolution tracked** when target responds

---

## Escalation vs Block vs Pause

| Action | When | Effect |
|--------|------|--------|
| **Escalate** | Need help/decision | Notifies PM, you can continue |
| **Block** | Waiting on another task | Status → blocked, can claim other work |
| **Pause** | Need to stop temporarily | Status → paused, state saved |

### Combining Actions

Often you'll combine:

```python
# Blocked AND need PM help
roboco_task_block(task_id, blocker_task_id)
roboco_task_escalate(task_id, "Blocked on auth service, need PM to coordinate")
```

---

## Good Escalation Format

```python
roboco_task_escalate(
    task_id="uuid-here",
    reason="""
ISSUE: API contract unclear
CONTEXT: Implementing user endpoint, acceptance criteria says "return user data"
QUESTION: Should I include sensitive fields (email, phone)? What about nested relations?
ATTEMPTED: Checked existing endpoints, no consistent pattern
BLOCKING: Can't proceed without this decision
"""
)
```

**Include:**
- What's the issue
- What context you have
- Specific question
- What you already tried
- How it's affecting work

---

## Responding to Escalations (PM)

When you receive an escalation:

1. **ACK immediately** - `roboco_notify_ack(notification_id)`
2. **Investigate** - Read task, journals, messages
3. **Decide** - Make the call or escalate further
4. **Communicate** - Message the agent with decision
5. **Unblock if needed** - `roboco_task_unblock(task_id)`

---

## Escalation Anti-Patterns

❌ **Don't escalate without trying first**
- Check documentation, journals, similar tasks

❌ **Don't escalate vague issues**
- "I'm stuck" → Instead: "Stuck on X because Y, tried Z"

❌ **Don't escalate too late**
- Escalate when you recognize you're blocked, not after hours of spinning

❌ **Don't skip levels**
- Developer → Cell PM → Main PM (don't skip Cell PM)

❌ **Don't escalate resolved issues**
- Only escalate if you actually need help
