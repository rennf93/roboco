# Task Management Tools

## Core Operations

| Tool | Purpose |
|------|---------|
| `roboco_task_get` | Get task details |
| `roboco_task_scan` | Find available tasks |
| `roboco_task_claim` | Take ownership |
| `roboco_task_start` | Begin work |

## Task Retrieval

```python
# Get specific task
task = roboco_task_get(task_id)

# Scan for available tasks
tasks = roboco_task_scan(
    team="backend",        # Optional filter
    status="pending"       # Optional filter
)
```

## Task Lifecycle

```python
# Claim task
roboco_task_claim(task_id)

# Start work (also resumes paused tasks)
roboco_task_start(task_id)

# Pause work
roboco_task_pause(task_id, reason="Waiting for clarification")

# Resume paused work (use start)
roboco_task_start(task_id)  # Works on paused tasks

# Block (waiting on another task)
roboco_task_block(task_id, blocker_task_id, reason)

# Unblock (PM only)
roboco_task_unblock(task_id)
```

## Submission

```python
# Submit for verification
roboco_task_submit_verification(task_id)

# Submit for QA
roboco_task_submit_qa(task_id, notes)

# QA actions
roboco_task_qa_pass(task_id, {notes: "..."})
roboco_task_qa_fail(task_id, {notes: "...", issues: [...]})

# Documentation complete
roboco_task_docs_complete(task_id)

# PM complete
roboco_task_complete(task_id)
```

## PM Operations

```python
# Create SUBTASK (most common)
roboco_task_create({
    title: "Implement auth endpoint",
    parent_task_id: my_task_id,  # REQUIRED for subtasks
    team: "backend",
    assigned_to: "be-dev-1"      # Use SLUG
})

# Create standalone task (rare)
roboco_task_create({
    title: "...",
    team: "backend",
    status: "backlog"
})

# Activate (backlog -> pending)
roboco_task_activate(task_id)

# Cancel
roboco_task_cancel(task_id, reason)

# Plan
roboco_task_plan(task_id, approach, steps)

# Escalate to CEO (parent tasks only)
roboco_task_escalate_to_ceo(task_id, notes)
```

**CRITICAL**: When creating subtasks, ALWAYS include `parent_task_id`. Without it, you create orphan sibling tasks instead of linked subtasks.

**Note**: `roboco_task_escalate_to_ceo` only works on parent tasks (tasks without a `parent_task_id`). Subtasks must have their parent task escalated instead.

## Progress Updates

```python
roboco_task_progress(task_id, "Implementing API", 50)
```
