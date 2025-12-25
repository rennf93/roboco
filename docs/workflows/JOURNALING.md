# Journaling Guide

## Purpose

Your journal is your **personal growth record**. It:
- Documents your decision-making process
- Tracks what you learned
- Records struggles for future reference
- Creates institutional memory
- Helps documenters understand your journey

---

## Journal Entry Types

### 1. General Entry (`roboco_journal_entry`)

Basic logging for day-to-day work.

```python
roboco_journal_entry({
    "type": "work_log",           # or "note", "observation"
    "title": "Started rate limiter implementation",
    "content": "Reviewing existing code patterns in auth module...",
    "task_id": "uuid-here",       # Link to current task
    "tags": ["rate-limiting", "redis"]
})
```

**When to use:**
- Starting work on a task
- Mid-task progress notes
- Observations about the codebase
- General thoughts

---

### 2. Decision Log (`roboco_journal_decision`)

**REQUIRED** when choosing between approaches.

```python
roboco_journal_decision({
    "title": "Chose Redis over in-memory for rate limiting",
    "context": "Need to implement rate limiting for API endpoints",
    "options": [
        "Redis sliding window",
        "In-memory with TTL",
        "Database-backed counter"
    ],
    "chosen": "Redis sliding window",
    "rationale": "Redis provides distributed state, TTL support, and scales horizontally. In-memory wouldn't work with multiple instances.",
    "task_id": "uuid-here"
})
```

**When to use:**
- Choosing between libraries/frameworks
- Architecture decisions
- Implementation approach selection
- Trade-off decisions

---

### 3. Task Reflection (`roboco_journal_reflect`)

**REQUIRED** when completing a task.

```python
roboco_journal_reflect({
    "task_id": "uuid-here",
    "title": "Rate Limiter Implementation Complete",
    "what_done": "Implemented Redis-based sliding window rate limiter with configurable limits per endpoint",
    "what_learned": "Redis MULTI/EXEC for atomic operations, Lua scripting for complex logic",
    "what_struggled": "Initially missed edge case with concurrent requests - had to add locking",
    "next_steps": "Consider adding rate limit headers to responses, document in API docs"
})
```

**When to use:**
- After submitting for QA
- After completing any significant task
- When handing off to documenter

---

### 4. Learning Entry (`roboco_journal_learning`)

Document new knowledge.

```python
roboco_journal_learning({
    "title": "Redis Lua Scripting for Atomic Operations",
    "what_learned": "Redis Lua scripts execute atomically - no need for separate locking when using EVAL",
    "how_applied": "Used in rate limiter to check and increment in single atomic operation",
    "source": "Redis documentation + trial and error",
    "task_id": "uuid-here"
})
```

**When to use:**
- Discovered something new about a technology
- Found a better pattern
- Learned from a mistake
- Picked up domain knowledge

---

### 5. Struggle Entry (`roboco_journal_struggle`)

Document challenges for future reference.

```python
roboco_journal_struggle({
    "title": "Race condition in concurrent rate limit checks",
    "what_struggled": "Multiple requests hitting rate limiter simultaneously were all passing before any count incremented",
    "attempted_solutions": [
        "Added Redis WATCH - didn't help with high concurrency",
        "Tried INCR with separate GET - still had race window"
    ],
    "resolution": "Used Lua script to make check+increment atomic",
    "help_needed": false,
    "task_id": "uuid-here"
})
```

**When to use:**
- Hit a blocker (even if resolved)
- Spent significant time debugging
- Found a non-obvious solution
- Need to request help (`help_needed: true`)

---

## When to Journal

| Moment | Entry Type |
|--------|------------|
| Start a task | `roboco_journal_entry` (work_log) |
| Make a decision | `roboco_journal_decision` |
| Learn something new | `roboco_journal_learning` |
| Hit a struggle | `roboco_journal_struggle` |
| Complete a task | `roboco_journal_reflect` |
| Make progress | `roboco_journal_entry` |

---

## Reading Journals

### Search Your Own Journal

```python
roboco_journal_search("rate limiting redis")  # Semantic search
roboco_journal_recent(limit=10)               # Recent entries
roboco_journal_recent(entry_type="decision_log")  # Filter by type
roboco_journal_recent(task_id="uuid-here")    # Filter by task
roboco_journal_stats()                        # Your stats
```

### Read Team Journals (PM/Documenter only)

```python
roboco_journal_read_team(
    target_agent="be-dev-1",
    task_id="uuid-here",      # Optional filter
    limit=10
)
roboco_journal_scope()        # See who you can read
```

---

## Access Permissions

| Your Role | Can Read Journals Of |
|-----------|---------------------|
| Developer | Own only |
| QA | Own only |
| Documenter | Own + cell members (for documentation) |
| Cell PM | Own + cell members |
| Main PM | Own + all Cell PMs |
| Auditor | Everyone |

---

## Best Practices

1. **Journal as you go** - Don't wait until end of task
2. **Include task_id** - Links entries to work
3. **Be specific** - Future you needs context
4. **Record failures** - Struggles are valuable learning
5. **Reflect honestly** - No one judges your struggles
6. **Tag consistently** - Helps with search
