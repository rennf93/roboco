# Journal Tools

There is **no** `roboco_journal_*` tool. Journaling is a single content tool on the `roboco-do` MCP server: `note`. The `scope` argument selects the entry kind; structured fields are filled per scope.

```python
note(
    text: str,                  # always: one-paragraph summary
    scope: str = "note",        # note | decision | reflect | learning | struggle
    task_id: str | None = None, # auto-filled from your active task if omitted
    title: str | None = None,
    # decision-scope fields:
    context: str = "",
    options=None,               # list of {name, pros, cons} (a single dict is ok)
    chosen: str = "",
    rationale: str = "",
    consequences=None,          # list of strings (a single string is ok)
    # reflect-scope fields:
    what_done: str = "",
    what_learned: str = "",
    what_struggled: str = "",
    next_steps=None,            # list of strings (a single string is ok)
)
```

`text` is always required. Missing narrative fields default to a visible placeholder rather than being rejected — the note is always recorded.

## Scopes

| Scope | Use For | Structured fields |
|-------|---------|-------------------|
| `note` | General entry | (just `text`) |
| `decision` | Decision log | `context`, `options`, `chosen`, `rationale`, `consequences` |
| `reflect` | Task reflection | `what_done`, `what_learned`, `what_struggled`, `next_steps` |
| `learning` | Learning capture | (just `text`) |
| `struggle` | Problem / blocker | (just `text`) |

## General Entry

```python
note(
    text="SCAN is better than KEYS for large datasets",
    scope="learning",
    title="Redis SCAN vs KEYS",
    task_id=task_id,
)
```

## Decision Log

```python
note(
    text="Chose Redis for session storage over PostgreSQL.",
    scope="decision",
    title="Session storage choice",
    context="Need fast session lookups",
    options=[
        {"name": "PostgreSQL", "pros": "durable", "cons": "slower reads"},
        {"name": "Redis", "pros": "sub-ms reads", "cons": "ephemeral"},
    ],
    chosen="Redis",
    rationale="Sub-ms reads, ephemeral data",
    consequences=["Session loss on Redis restart is acceptable"],
)
```

## Learning

```python
note(
    text="asyncio.gather for parallel calls — reduced latency 50%",
    scope="learning",
    title="Parallel async calls",
)
```

## Struggle (Problem / Blocker)

```python
note(
    text=(
        "Tests failing intermittently — tried timeout increase and retry "
        "logic; root cause was a race condition in setup."
    ),
    scope="struggle",
    task_id=task_id,
)
```

## Reflection

Use a `reflect`-scope note before submitting to QA — it gives QA the "why" behind the diff.

```python
note(
    text="Implemented rate limiting with a Redis-backed sliding window.",
    scope="reflect",
    task_id=task_id,
    what_done="Implemented rate limiting",
    what_learned="Lua scripts give atomicity for the counter increment",
    what_struggled="Testing concurrency deterministically",
    next_steps=["Add a load test for the 100-req boundary"],
)
```

## Reading Journals

Journals are written by `note` and surface through the knowledge base — there is no separate journal-read tool. Search past notes (yours and your team's, where permitted) via the `roboco-optimal` MCP server:

```python
# Semantic search over indexed notes/decisions/learnings
roboco_kb_search(query="rate limiting", index_types=["journals", "decisions"])

# Conversational lookup with follow-up context
roboco_ask_mentor(question="What did we decide about session storage?")
```
