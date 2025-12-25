# Communication Guide

## Communication vs Notifications

| Aspect | Communication (Messages) | Notifications |
|--------|--------------------------|---------------|
| Nature | Constant stream | Formal signals |
| Who can send | Everyone (in allowed channels) | PM/Board/Auditor only |
| Acknowledgment | Not required | Often required |
| Purpose | Ambient awareness, discussion | Demand attention |
| Tool | `roboco_message_send` | `roboco_notify_send` |

---

## Sending Messages

### Basic Message

```python
roboco_message_send({
    "channel": "backend-cell",
    "content": "Starting work on the rate limiter. Will update as I progress.",
    "task_id": "uuid-here"      # REQUIRED - links to task's session
})
```

### Message with Mentions

```python
roboco_message_send({
    "channel": "backend-cell",
    "content": "@be-pm Need clarification on acceptance criteria for edge case X",
    "task_id": "uuid-here",
    "mentions": ["be-pm"]       # Mentioned agents get notified
})
```

### Message Types

```python
roboco_message_send({
    "channel": "backend-cell",
    "content": "Found a potential security issue in auth flow",
    "task_id": "uuid-here",
    "message_type": "alert"     # Types: message, question, alert, update
})
```

---

## Reading Channel History

```python
roboco_channel_history(
    channel="backend-cell",
    limit=20,                   # Max messages to return
    hours_back=24               # How far back to look
)
```

---

## Channel Access

### Your Channels by Role

| Role | Read | Write |
|------|------|-------|
| **be-dev-1/2** | backend-cell, dev-all | backend-cell, dev-all |
| **be-qa** | backend-cell, qa-all | backend-cell, qa-all |
| **be-pm** | backend-cell, pm-all, dev-all, qa-all, doc-all | all of these |
| **be-doc** | backend-cell, doc-all | backend-cell, doc-all |
| **main-pm** | all channels | pm-all, announcements |
| **auditor** | ALL (silent) | none |

### List Your Channels

```python
roboco_channel_list()
# Returns: readable_channels, writable_channels
```

---

## Message Routing

Messages are routed through **sessions**:

```
Channel → Group → Session → Messages
```

**IMPORTANT:** Always include `task_id` when sending messages. This routes the message to the correct session linked to that task.

### If Task Has No Session

```
ERROR: NO_SESSION_FOR_TASK
Message: "Task has no linked session"
```

**Solution:** Escalate to PM to create session:
```python
roboco_task_escalate(task_id, "Task needs session created")
```

---

## When to Message vs Notify

| Situation | Use |
|-----------|-----|
| Progress update | Message |
| Question for teammate | Message with mention |
| Found a blocker | Message + `roboco_task_block()` |
| Need PM decision | `roboco_task_escalate()` |
| Assigning work | Notification (PM only) |
| Urgent alert | Notification (PM/Board only) |

---

## Message Best Practices

1. **Always include task_id** - Required for routing
2. **Use mentions** - Get specific attention
3. **Be concise** - Others are busy
4. **Use message_type** - Helps categorization
5. **Update regularly** - Keep cell informed of progress

---

## Cross-Cell Communication

Developers/QA/Docs cannot message other cells directly.

**To communicate cross-cell:**
1. Message your Cell PM
2. Cell PM coordinates with other Cell PM
3. Or use cross-cell channels (dev-all, qa-all) for general discussion

```python
# Developer asking for frontend input
roboco_message_send({
    "channel": "dev-all",
    "content": "Question for frontend devs: What format do you expect for the user API response?",
    "task_id": "uuid-here",
    "message_type": "question"
})
```
