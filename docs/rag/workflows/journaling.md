# Journaling Workflow

## Why Journal

1. Becomes searchable knowledge for future agents
2. Helps with task handoffs
3. Documents decisions and learnings
4. Required before key transitions

## The Tool

Journaling is a single content tool: `note(text, scope, ...)` on the
`roboco-do` MCP server. There is **no** separate `roboco_journal_*` tool —
the `scope` argument selects the kind of entry.

| `scope` | Use For |
|---------|---------|
| `note` (default) | General observation |
| `reflect` | End-of-task summary (what done / learned / struggled) |
| `decision` | Architectural decision (context / options / chosen / rationale) |
| `learning` | New knowledge gained |
| `struggle` | Problems and solutions |

## Creating Entries

```python
# General entry
note(
    text="SCAN is better than KEYS for large Redis datasets",
    scope="learning",
    task_id=task_id,
)

# Decision log — `decision` scope uses the structured fields
note(
    text="Chose Redis for session storage",
    scope="decision",
    task_id=task_id,
    context="Need fast session lookups, ephemeral data",
    options=[
        {"name": "PostgreSQL", "pros": "durable", "cons": "slower"},
        {"name": "Redis", "pros": "sub-ms reads", "cons": "ephemeral"},
        {"name": "In-memory", "pros": "fastest", "cons": "lost on restart"},
    ],
    chosen="Redis",
    rationale="Sub-millisecond reads; data is ephemeral by design",
    consequences=["Adds Redis as a session dependency"],
)

# Struggle (problem and solution)
note(
    text="Tests failing intermittently; root cause was a setup race condition",
    scope="struggle",
    task_id=task_id,
)
```

`options`, `consequences`, and `next_steps` accept either a list or a
single value. For `decision` and `reflect` scopes the structured fields
are recommended; the note is always recorded even if some are omitted.

## Required Reflections

Before submitting for QA or completing, write a `reflect` entry:

```python
note(
    text="Implemented rate limiting with Redis",
    scope="reflect",
    task_id=task_id,
    what_done="Redis-backed token bucket on the API edge",
    what_learned="Lua scripts give atomic check-and-decrement",
    what_struggled="Testing concurrent requests deterministically",
    next_steps=["Add a regression test for the boundary case"],
)
```

## Searching Journals

Journal entries are indexed into the knowledge base. Search them through
the `roboco-optimal` RAG tools (there is no dedicated journal-search verb):

```python
# Semantic search across the KB, filtered to journal entries
roboco_kb_search(query="rate limiting patterns", index_types=["journals"])

# Or ask the mentor, which searches all sources including journals
roboco_ask_mentor(question="What did we decide about rate limiting?")
```

## Best Practices

1. **Journal as you go** - Don't wait until end
2. **Be specific** - Generic entries are less searchable
3. **Record failures** - They're valuable learning (`scope="struggle"`)
4. **Use the right scope** - `decision` / `reflect` light up the panel views
5. **Include context** - Future searchers need it
