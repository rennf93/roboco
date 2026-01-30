# Proactive Knowledge Injection

System automatically provides relevant context when you claim a task.

## How It Works

```
You claim task --> System searches KB --> Context injected --> You start informed
```

When you claim a task, the system:
1. Searches for similar completed tasks
2. Finds relevant learnings from other agents
3. Gets applicable coding/security standards
4. Retrieves recent architectural decisions
5. Identifies known issues in related areas
6. Finds relevant code patterns

## Getting Context

```python
# Context is auto-stored on task claim
# Retrieve it when starting work:
roboco_get_proactive_context(task_id="your-task-id")
```

**Returns:**

| Field | Description |
|-------|-------------|
| `similar_tasks` | Completed tasks with similar descriptions |
| `relevant_learnings` | Insights from other agents |
| `applicable_standards` | Rules that apply to this work |
| `recent_decisions` | Related architectural choices |
| `known_issues` | Problems to watch out for |
| `code_patterns` | Relevant code examples |
| `summary` | AI-generated context summary |

## Example Response

```json
{
  "status": "success",
  "source": "stored",
  "similar_tasks": [
    {
      "id": "abc-123",
      "title": "Add user authentication",
      "completion_notes": "Used JWT with refresh tokens"
    }
  ],
  "relevant_learnings": [
    {
      "content": "Always validate JWT expiry server-side",
      "agent": "be-dev-1",
      "category": "security"
    }
  ],
  "applicable_standards": [
    {
      "rule": "Use Pydantic for request validation",
      "severity": "required"
    }
  ],
  "summary": "Similar auth work done. Use JWT pattern from task abc-123."
}
```

## Workflow

### 1. Claim Task

```python
roboco_task_claim(task_id="my-task")
# System auto-generates proactive context
```

### 2. Start Work

```python
# Get the context that was prepared for you
context = roboco_get_proactive_context(task_id="my-task")

# Review what's relevant
print(context["summary"])
print(context["similar_tasks"])
```

### 3. Apply Knowledge

Use the context to:
- Avoid repeating past mistakes
- Follow established patterns
- Build on previous decisions
- Learn from others' experiences

## Force Refresh

If context seems stale:

```python
roboco_get_proactive_context(
    task_id="my-task",
    force_refresh=True  # Skip stored, generate fresh
)
```

## Best Practices

1. **Always check context** when starting a task
2. **Read similar tasks** - learn from past work
3. **Note applicable standards** - avoid violations
4. **Check known issues** - prevent repeating problems
5. **Review learnings** - benefit from others' insights
