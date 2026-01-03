# Journaling Guide

> **Status:** Implemented
>
> This document describes the Journal API and how agents use it for personal growth tracking.

---

## Purpose

Your journal is your **personal growth record**. It:
- Documents your decision-making process
- Tracks what you learned
- Records struggles for future reference
- Creates institutional memory
- Helps documenters understand your journey
- Enables semantic search for past experiences

---

## Journal API Endpoints

### Your Journal (`/me`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/journals/me` | GET | Get or create your journal |
| `/journals/me/entries` | GET | List your entries |
| `/journals/me/entries` | POST | Create a general entry |
| `/journals/me/stats` | GET | Get your journal statistics |
| `/journals/me/growth` | GET | Get your growth metrics |
| `/journals/me/search` | POST | Semantic search your journal |
| `/journals/me/reflections` | POST | Add task reflection |
| `/journals/me/decisions` | POST | Add decision log |
| `/journals/me/learnings` | POST | Add learning entry |
| `/journals/me/struggles` | POST | Add struggle entry |
| `/journals/me/notes` | POST | Add general note |

### Other Agent Journals (with permission)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/journals/{agent_id}` | GET | Get another agent's journal |
| `/journals/{agent_id}/entries` | GET | List another agent's entries |
| `/journals/entries/{entry_id}` | GET | Get specific entry |
| `/journals/entries/{entry_id}` | DELETE | Delete your own entry |

---

## Journal Entry Types

### 1. General Entry

Basic logging for day-to-day work:

```python
roboco_journal_entry({
    "type": "work_log",           # or "note", "observation"
    "title": "Started rate limiter implementation",
    "content": "Reviewing existing code patterns in auth module...",
    "task_id": "uuid-here",       # Link to current task
    "session_id": "uuid-here",    # Link to session (optional)
    "tags": ["rate-limiting", "redis"],
    "is_private": false           # Default: false
})
```

**When to use:**
- Starting work on a task
- Mid-task progress notes
- Observations about the codebase
- General thoughts

---

### 2. Decision Log

**RECOMMENDED** when choosing between approaches:

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
    "consequences": "Added Redis dependency, need to handle connection failures",
    "task_id": "uuid-here",
    "tags": ["architecture", "rate-limiting"]
})
```

**When to use:**
- Choosing between libraries/frameworks
- Architecture decisions
- Implementation approach selection
- Trade-off decisions

---

### 3. Task Reflection

**RECOMMENDED** when completing a task:

```python
roboco_journal_reflect({
    "task_id": "uuid-here",
    "title": "Rate Limiter Implementation Complete",
    "what_done": "Implemented Redis-based sliding window rate limiter with configurable limits per endpoint",
    "what_learned": "Redis MULTI/EXEC for atomic operations, Lua scripting for complex logic",
    "what_struggled": "Initially missed edge case with concurrent requests - had to add locking",
    "next_steps": "Consider adding rate limit headers to responses, document in API docs",
    "tags": ["implementation", "rate-limiting"]
})
```

**When to use:**
- After submitting for QA
- After completing any significant task
- When handing off to documenter

---

### 4. Learning Entry

Document new knowledge:

```python
roboco_journal_learning({
    "title": "Redis Lua Scripting for Atomic Operations",
    "what_learned": "Redis Lua scripts execute atomically - no need for separate locking when using EVAL",
    "how_applied": "Used in rate limiter to check and increment in single atomic operation",
    "source": "Redis documentation + trial and error",
    "task_id": "uuid-here",
    "tags": ["redis", "lua", "atomic-operations"]
})
```

**When to use:**
- Discovered something new about a technology
- Found a better pattern
- Learned from a mistake
- Picked up domain knowledge

---

### 5. Struggle Entry

Document challenges for future reference:

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
    "task_id": "uuid-here",
    "tags": ["race-condition", "concurrency", "redis"]
})
```

**When to use:**
- Hit a blocker (even if resolved)
- Spent significant time debugging
- Found a non-obvious solution
- Need to request help (`help_needed: true`)

---

## When to Journal

| Moment | Entry Type | Tool |
|--------|------------|------|
| Start a task | General entry | `roboco_journal_entry` |
| Make a decision | Decision log | `roboco_journal_decision` |
| Learn something new | Learning | `roboco_journal_learning` |
| Hit a struggle | Struggle | `roboco_journal_struggle` |
| Complete a task | Reflection | `roboco_journal_reflect` |
| Make progress | General entry | `roboco_journal_entry` |
| Quick note | General note | `roboco_journal_entry` |

---

## Reading Journals

### Search Your Own Journal

Semantic search (uses RAG):

```python
roboco_journal_search({
    "query": "rate limiting redis",
    "top_k": 5
})
```

### List Your Entries

```python
# Recent entries
roboco_journal_recent(limit=10)

# Filter by type
roboco_journal_recent(entry_type="decision_log")

# Filter by task
roboco_journal_recent(task_id="uuid-here")
```

### Your Statistics

```python
roboco_journal_stats()
# Returns: total_entries, entries_by_type, last_entry_at, has_summary
```

### Your Growth Metrics

```python
roboco_journal_growth()
# Returns:
#   total_reflections, total_learnings, total_struggles, total_decisions,
#   struggle_resolution_rate, learning_frequency, sentiment_trend
```

---

## Reading Other Agents' Journals

Access is based on cell membership and role hierarchy:

```python
# By agent slug
roboco_journal_read("be-dev-1")

# By agent UUID
roboco_journal_read("a1b2c3d4-...")

# List entries with filters
roboco_journal_read_entries(
    agent_id="be-dev-1",
    entry_type="decision_log",
    task_id="uuid-here",
    limit=10
)
```

---

## Access Permissions

The Journal API enforces strict access controls based on cell membership:

| Your Role | Can Read Journals Of |
|-----------|---------------------|
| Developer | Own only |
| QA | Own only |
| Documenter | Own + cell members (for documentation) |
| Cell PM | Own + cell members |
| Main PM | Own + all Cell PMs |
| Auditor | Everyone (silent observer) |
| CEO | Everyone |

### Cell Membership

- **Backend Cell**: be-dev-1, be-dev-2, be-qa, be-pm, be-doc
- **Frontend Cell**: fe-dev-1, fe-dev-2, fe-qa, fe-pm, fe-doc
- **UX/UI Cell**: ux-dev-1, ux-dev-2, ux-qa, ux-pm, ux-doc

Cell members with access can see ALL entries from each other, including private ones.

---

## Private Entries

Mark entries as private when they contain sensitive reflections:

```python
roboco_journal_entry({
    "type": "observation",
    "title": "Personal note on team dynamics",
    "content": "...",
    "is_private": true
})
```

**Note**: Cell members with journal access can see your private entries. This is by design - journals are for team learning, not secrets.

---

## Entry Response Format

All entry endpoints return:

```json
{
    "id": "uuid",
    "journal_id": "uuid",
    "type": "decision_log",
    "title": "...",
    "content": "...",
    "task_id": "uuid or null",
    "session_id": "uuid or null",
    "timestamp": "2025-01-15T10:30:00Z",
    "tags": ["tag1", "tag2"],
    "sentiment": "positive|neutral|negative|null",
    "is_private": false,
    "created_at": "...",
    "updated_at": "..."
}
```

---

## Best Practices

1. **Journal as you go** - Don't wait until end of task
2. **Include task_id** - Links entries to work for context
3. **Be specific** - Future you needs context
4. **Record failures** - Struggles are valuable learning
5. **Reflect honestly** - No one judges your struggles
6. **Tag consistently** - Helps with search and filtering
7. **Use structured types** - Decision logs, reflections, learnings are searchable
8. **Link sessions** - Include session_id when working in a session

---

## Integration with Task Workflow

### On Claim
```python
roboco_journal_entry({
    "type": "work_log",
    "title": f"Claimed task: {task.title}",
    "content": "Initial assessment: ...",
    "task_id": task_id
})
```

### On Decision
```python
roboco_journal_decision({
    "title": "Implementation approach",
    "context": "...",
    "options": [...],
    "chosen": "...",
    "rationale": "...",
    "task_id": task_id
})
```

### On Completion
```python
roboco_journal_reflect({
    "task_id": task_id,
    "title": f"Completed: {task.title}",
    "what_done": "...",
    "what_learned": "...",
    "what_struggled": "...",
    "next_steps": "..."
})
```

---

## Growth Metrics Explained

The growth metrics endpoint tracks your development over time:

| Metric | Description |
|--------|-------------|
| `total_reflections` | Number of task reflections |
| `total_learnings` | Number of learning entries |
| `total_struggles` | Number of struggle entries |
| `total_decisions` | Number of decision logs |
| `struggle_resolution_rate` | % of struggles with resolutions |
| `learning_frequency` | Learnings per time period |
| `sentiment_trend` | Overall sentiment direction (improving, stable, declining) |
