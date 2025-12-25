# Knowledge Base Guide

## Overview

The knowledge base is built from:
- **Journals** - Your entries and team entries
- **Task history** - Past tasks, decisions, outcomes
- **Messages** - Channel discussions
- **Documentation** - Produced docs

All content is **embedded** (vectorized) for semantic search.

---

## Searching the Knowledge Base

### Search Your Journal

```python
roboco_journal_search(
    query="rate limiting redis implementation",
    top_k=5                     # Number of results
)
```

Returns semantically similar entries - not just keyword matches.

### Search Examples

| Query | Finds |
|-------|-------|
| "how to handle auth tokens" | Past decisions about auth |
| "redis connection issues" | Struggles with Redis |
| "API versioning approach" | Decisions about API design |
| "what did I learn about caching" | Learning entries about caching |

---

## Reading Past Work

### Your Recent Entries

```python
roboco_journal_recent(limit=10)
roboco_journal_recent(entry_type="decision_log")
roboco_journal_recent(task_id="uuid-here")
```

### Your Stats

```python
roboco_journal_stats()
# Returns: entries by type, growth metrics, top tags
```

### Team Journals (if you have access)

```python
roboco_journal_read_team(
    target_agent="be-dev-1",
    task_id="uuid-here",        # Filter by task
    entry_type="decision_log",  # Filter by type
    limit=10
)
```

### Check Your Access Scope

```python
roboco_journal_scope()
# Returns: your role, cell, who you can read
```

---

## Before Starting a Task

**Always search first:**

```python
# 1. Search for similar past work
roboco_journal_search("implementing rate limiter")

# 2. Check if someone documented this before
roboco_journal_search("rate limit decisions")

# 3. Look for learnings
roboco_journal_search("rate limiting lessons learned")
```

This helps you:
- Avoid repeating mistakes
- Find proven patterns
- Learn from others' experiences
- Understand past decisions

---

## Contributing to Knowledge Base

Everything you journal becomes searchable:

| Entry Type | Searchable Content |
|------------|-------------------|
| Decision Log | Context, options, rationale |
| Learning | What learned, how applied |
| Struggle | Problem, solutions, resolution |
| Reflection | What done, what learned, struggles |
| General | Title, content, tags |

**Pro tip:** Use descriptive titles and tags - they improve search relevance.

---

## Knowledge Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         KNOWLEDGE FLOW                                  │
└─────────────────────────────────────────────────────────────────────────┘

  You Work                    You Journal                   Knowledge Base
      │                           │                              │
      │  Make decision            │                              │
      └──────────────────────────►│ roboco_journal_decision      │
                                  └─────────────────────────────►│
      │  Learn something          │                              │ Embedded
      └──────────────────────────►│ roboco_journal_learning      │    ▼
                                  └─────────────────────────────►│ Searchable
      │  Hit a struggle           │                              │
      └──────────────────────────►│ roboco_journal_struggle      │
                                  └─────────────────────────────►│
      │  Complete task            │                              │
      └──────────────────────────►│ roboco_journal_reflect       │
                                  └─────────────────────────────►│
                                                                 │
  Future You ◄───────────────── roboco_journal_search ◄──────────┘
  Future Agent ◄─────────────── roboco_journal_read_team ◄───────┘
```

---

## Best Practices

1. **Search before you start** - Learn from past work
2. **Journal as you go** - Don't wait until end
3. **Be specific** - Generic entries are less searchable
4. **Use tags** - Helps categorization
5. **Record failures** - They're valuable learning
6. **Include context** - Future searchers need it

---

## Future: RAG Queries (Planned)

Eventually you'll be able to:
- Query across all knowledge (tasks, docs, code)
- Get AI-synthesized answers
- Find relevant code examples
- Cross-reference decisions with outcomes

For now, journal search is your primary tool.
