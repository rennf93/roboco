# Escalation Guide

> **Status:** Implemented
>
> This document describes the task escalation system and CEO approval workflow.

---

## Escalation Chain

The escalation chain is strictly enforced - you cannot skip levels:

```
Developer/QA/Documenter
         │
         ▼
      Cell PM (be-pm, fe-pm, ux-pm)
         │
         ▼
      Main PM (main-pm)
         │
         ▼
   Product Owner (product-owner)
         │
         ▼
        CEO (ceo)
```

### Detailed Chain

| Agent | Escalates To |
|-------|--------------|
| be-dev-1, be-dev-2 | be-pm |
| be-qa | be-pm |
| be-doc | be-pm |
| fe-dev-1, fe-dev-2 | fe-pm |
| fe-qa | fe-pm |
| fe-doc | fe-pm |
| ux-dev-1, ux-dev-2 | ux-pm |
| ux-qa | ux-pm |
| ux-doc | ux-pm |
| be-pm, fe-pm, ux-pm | main-pm |
| main-pm | product-owner |
| product-owner | ceo |
| head-marketing | ceo |
| auditor | ceo |

---

## Types of Escalation

### 1. Task Escalation (`roboco_task_escalate`)

Used when you need help with a specific task. Available to ALL agents.

```python
roboco_task_escalate(
    task_id="uuid-here",
    reason="Need clarification on API contract - acceptance criteria unclear"
)
```

**Key Points:**
- Auto-routes to your escalation target (you cannot specify a different target)
- Creates a high-priority notification requiring acknowledgment
- Task status remains unchanged (you can keep working if possible)
- Escalation is logged in task history

### 2. CEO Escalation (`roboco_task_escalate_to_ceo`)

PM-only. Used for major tasks requiring CEO sign-off:

```python
roboco_task_escalate_to_ceo(
    task_id="uuid-here",
    notes="Major feature ready for final review"
)
```

**Requirements:**
- Task must be in `awaiting_pm_review` status
- For git tasks, PR must exist (`pr_number` must be set)
- Only PMs (cell_pm, main_pm) can escalate to CEO

**Result:**
- Status changes to `awaiting_ceo_approval`
- CEO receives high-priority notification requiring ACK

### 3. Soft Block Escalation

When blocked by external factors (not another task):

```python
roboco_task_soft_block(
    task_id="uuid-here",
    reason="Waiting for production API credentials",
    blocker_type="external_dependency",
    what_needed="AWS credentials for production environment"
)
```

**Result:**
- Status changes to `blocked`
- PM receives notification with ACTION REQUIRED
- PM MUST call `roboco_task_unblock()` when resolved
- Verbal resolution in chat is NOT enough

---

## When to Escalate

| Situation | Escalate To | Tool |
|-----------|-------------|------|
| Need PM decision | Cell PM | `roboco_task_escalate` |
| Blocked by external factor | Cell PM | `roboco_task_soft_block` |
| Blocked by another task | Cell PM | `roboco_task_block` + `roboco_task_escalate` |
| Cross-cell coordination needed | Cell PM (routes to Main PM) | `roboco_task_escalate` |
| Scope creep beyond task | Cell PM | `roboco_task_escalate` |
| Resource/priority conflict | Cell PM | `roboco_task_escalate` |
| Major feature ready for merge | CEO | `roboco_task_escalate_to_ceo` (PM only) |

---

## CEO Approval Workflow

For major tasks (parent tasks, high-priority features, breaking changes):

```
                    awaiting_pm_review
                           │
                           ▼
         PM reviews and decides to escalate
                           │
        roboco_task_escalate_to_ceo(task_id, notes)
                           │
                           ▼
                   awaiting_ceo_approval
                           │
          ┌────────────────┴────────────────┐
          │                                 │
    CEO APPROVES                      CEO REJECTS
          │                                 │
 roboco_task_ceo_approve()       roboco_task_ceo_reject(notes)
          │                                 │
          ▼                                 ▼
      completed                       needs_revision
                                           │
                                    (assigned back to
                                     original developer)
```

### CEO Approval Queue

PMs can view tasks awaiting CEO approval:

```python
# Get all tasks awaiting CEO approval (org-wide)
roboco_tasks_awaiting_ceo()
```

### CEO Actions

```python
# Approve and complete
roboco_task_ceo_approve(task_id, notes="Approved. Great work!")

# Reject and send back for revision
roboco_task_ceo_reject(task_id, notes="Need to address X before merge")
```

---

## Force Completion (CEO Only)

When subtasks are cancelled but parent should complete:

```python
roboco_task_complete(
    task_id="uuid-here",
    force_with_cancelled=True,
    justification="Subtask TASK-123 cancelled - functionality no longer needed"
)
```

**Requirements:**
- Only CEO can use `force_with_cancelled`
- Justification is required
- Does NOT work for pending/in_progress subtasks (only cancelled)

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
| Low context | "Need more background on why this was designed this way" |

### QA Escalations

| Reason | Example |
|--------|---------|
| Can't reproduce | "Bug not reproducible with given steps" |
| Unclear test criteria | "Don't know what 'acceptable performance' means" |
| Blocking issue found | "Critical security flaw - should we halt?" |
| Test environment issue | "Staging is down, can't proceed" |

### Documenter Escalations

| Reason | Example |
|--------|---------|
| Missing context | "Developer journal doesn't explain design decisions" |
| Scope question | "Should I document internal APIs?" |
| Access needed | "Can't view the code changes" |

### Cell PM Escalations

| Reason | Example |
|--------|---------|
| Cross-cell dependency | "Need frontend to expose new endpoint" |
| Resource conflict | "Both tasks need be-dev-1, can't parallelize" |
| Priority question | "Two P1 tasks - which first?" |
| Scope change | "Requirements changed mid-sprint" |

---

## What Happens When You Escalate

1. **Escalation notification sent** to your escalation target
2. **Notification is high-priority** and requires acknowledgment
3. **Task status unchanged** (you can keep working if possible)
4. **Target MUST ACK** the notification
5. **Target investigates** and responds
6. **For blocks**: PM must call `roboco_task_unblock()` when resolved

---

## Escalation vs Block vs Pause vs Substitute

| Action | When | Status Change | Tool |
|--------|------|---------------|------|
| **Escalate** | Need help/decision | No change | `roboco_task_escalate` |
| **Block (hard)** | Waiting on another task | → blocked | `roboco_task_block` |
| **Block (soft)** | Waiting on external factor | → blocked | `roboco_task_soft_block` |
| **Pause** | Need to stop temporarily | → paused | `roboco_task_pause` |
| **Substitute** | Can't continue, release task | → pending/awaiting_pm_review | `roboco_task_substitute` |

### Combining Actions

Often you'll combine:

```python
# Blocked AND need PM help
roboco_task_soft_block(
    task_id,
    "Waiting for API access",
    "external_dependency",
    "Need production API keys from DevOps"
)
# PM will be notified automatically
```

---

## Substitution (Graceful Exit)

When you can't continue a task:

```python
roboco_task_substitute(
    task_id="uuid-here",
    reason="low_context",
    details="Need more background on the authentication system design"
)
```

### Substitution Reasons

| Reason | Result Status | Use When |
|--------|---------------|----------|
| `task_complete` | awaiting_qa | Finished work, releasing for review |
| `low_context` | pending | Insufficient context to continue |
| `out_of_scope_team` | pending | Task belongs to different team |
| `out_of_scope_role` | pending | Task requires different role |
| `max_retries` | pending | Exceeded retry limit |
| `blocked_external` | blocked | Need skills outside your capabilities |

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

**CRITICAL**: For soft blocks, verbal resolution is NOT enough. You MUST call:
```python
roboco_task_unblock(task_id)
```

---

## Escalation Anti-Patterns

- **Don't escalate without trying first** - Check documentation, journals, similar tasks
- **Don't escalate vague issues** - "I'm stuck" -> Instead: "Stuck on X because Y, tried Z"
- **Don't escalate too late** - Escalate when you recognize you're blocked, not after hours of spinning
- **Don't skip levels** - Developer -> Cell PM -> Main PM (can't skip Cell PM)
- **Don't escalate resolved issues** - Only escalate if you actually need help
- **Don't bypass the chain** - The `escalate_to` parameter is validated against your escalation target
