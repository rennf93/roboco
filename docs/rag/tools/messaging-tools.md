# Messaging Tools

## Sending Messages

```python
roboco_message_send({
    channel: "backend-cell",
    content: "Starting work on rate limiting",
    task_id: task_id
})
```

## Channel History

```python
# Read channel history
roboco_channel_history(
    channel="backend-cell",
    limit=50
)
```

## Notifications

### Sending (PM/Board only)

```python
roboco_notify_send({
    recipient: "be-dev-1",
    type: "task_assignment",
    task_id: task_id,
    message: "Task ready for you"
})
```

### Receiving

```python
# List notifications
notifications = roboco_notify_list()

# Acknowledge
roboco_notify_ack(notification_id)
```

### Notification Types

| Type | Purpose |
|------|---------|
| `task_assignment` | New task assigned |
| `priority_change` | Priority updated |
| `blocker_escalation` | Task blocked |
| `review_request` | Review needed |
| `documentation_request` | Docs needed |
| `alert` | General alert |
| `broadcast` | Org-wide message |

## Sessions

```python
# Create session for tasks
roboco_session_create_for_tasks({
    title: "Feature X Implementation",
    task_ids: [task_1_id, task_2_id]
})

# Start collaborative session
roboco_session_start(
    channel="backend-cell",
    session_type="collaborative",
    task_id=task_id
)
```

## Message Types

| Type | Use For |
|------|---------|
| `reasoning` | Thought process |
| `dialogue` | Discussion |
| `decision` | Decisions made |
| `action` | Actions taken |
| `blocker` | Blocking issues |
| `technical` | Technical details |
