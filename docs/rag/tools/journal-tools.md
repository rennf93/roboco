# Journal Tools

## Creating Entries

| Tool | Purpose |
|------|---------|
| `roboco_journal_entry` | General entry |
| `roboco_journal_decision` | Decision log |
| `roboco_journal_learning` | Learning capture |
| `roboco_journal_struggle` | Problem/solution |
| `roboco_journal_reflect` | Task reflection |

## General Entry

```python
roboco_journal_entry({
    type: "learning",
    title: "Redis SCAN vs KEYS",
    content: "SCAN is better for large datasets",
    task_id: task_id,
    tags: ["redis", "performance"]
})
```

Entry types: `task_reflection`, `decision_log`, `learning`, `struggle`, `general`

## Decision Log

```python
roboco_journal_decision({
    title: "Session storage choice",
    context: "Need fast session lookups",
    options: ["PostgreSQL", "Redis"],
    chosen: "Redis",
    rationale: "Sub-ms reads, ephemeral data"
})
```

## Learning

```python
roboco_journal_learning({
    content: "asyncio.gather for parallel calls",
    how_applied: "Reduced latency 50%",
    category: "performance",
    tags: ["async"]
})
```

## Struggle (Problem/Solution)

```python
roboco_journal_struggle({
    task_id: task_id,
    problem: "Tests failing intermittently",
    attempts: ["Timeout increase", "Retry logic"],
    resolution: "Race condition in setup"
})
```

## Reflection (Required)

```python
roboco_journal_reflect({
    task_id: task_id,
    what_done: "Implemented rate limiting",
    what_learned: "Lua scripts for atomicity",
    what_struggled: "Testing concurrency"
})
```

## Reading Journals

```python
# Search your journal
roboco_journal_search("rate limiting", top_k=5)

# Recent entries
roboco_journal_recent(limit=10)

# Read team journals (if permitted)
roboco_journal_read_team(
    target_agent="be-dev-1",
    task_id=task_id
)

# Your stats
roboco_journal_stats()

# Check access scope
roboco_journal_scope()
```
