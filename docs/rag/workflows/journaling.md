# Journaling Workflow

## Why Journal

1. Becomes searchable knowledge for future agents
2. Helps with task handoffs
3. Documents decisions and learnings
4. Required before key transitions

## Entry Types

| Type | Use For |
|------|---------|
| `task_reflection` | End of task summary |
| `decision_log` | Architectural decisions |
| `learning` | New knowledge gained |
| `struggle` | Problems and solutions |
| `general` | Other observations |

## Creating Entries

```python
# General entry
roboco_journal_entry({
    type: "learning",
    title: "Redis SCAN vs KEYS",
    content: "SCAN is better for large datasets",
    task_id: task_id,
    tags: ["redis", "performance"]
})

# Decision log
roboco_journal_decision({
    title: "Session storage choice",
    context: "Need fast session lookups",
    options: ["PostgreSQL", "Redis", "In-memory"],
    chosen: "Redis",
    rationale: "Sub-millisecond reads, ephemeral data"
})

# Struggle (problem and solution)
roboco_journal_struggle({
    task_id: task_id,
    problem: "Tests failing intermittently",
    attempts: ["Increased timeout", "Added retry"],
    resolution: "Race condition in setup"
})

# Learning
roboco_journal_learning({
    content: "Use asyncio.gather for parallel calls",
    how_applied: "Reduced endpoint latency 50%",
    category: "performance",
    tags: ["async", "performance"]
})
```

## Required Reflections

Before submitting for QA or completing:

```python
roboco_journal_reflect({
    task_id: task_id,
    what_done: "Implemented rate limiting with Redis",
    what_learned: "Lua scripts for atomic operations",
    what_struggled: "Testing concurrent requests"
})
```

## Searching Journals

```python
# Semantic search your journal
roboco_journal_search("rate limiting patterns", top_k=5)

# Search team journals (if permitted)
roboco_journal_read_team("be-dev-1", task_id=task_id)
```

## Best Practices

1. **Journal as you go** - Don't wait until end
2. **Be specific** - Generic entries are less searchable
3. **Use tags** - Helps categorization
4. **Record failures** - They're valuable learning
5. **Include context** - Future searchers need it
