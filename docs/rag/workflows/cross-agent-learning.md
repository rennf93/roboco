# Cross-Agent Learning

When one agent learns something, all agents benefit. The learning network enables organizational knowledge to compound over time.

## Recording Learnings

When you discover something useful, record it:

```python
roboco_record_learning(
    content="Use transactions for multi-table updates to prevent partial writes",
    category="pattern",
    team="backend",      # Optional: backend, frontend, ux_ui
    shareable=True,      # Default: True
    tags=["database", "transactions", "consistency"]
)
```

## Learning Categories

| Category | When to Use |
|----------|-------------|
| `error_handling` | How to handle specific errors |
| `performance` | Optimization techniques |
| `testing` | Testing strategies and patterns |
| `pattern` | Code patterns and idioms |
| `architecture` | Design decisions and trade-offs |
| `security` | Security best practices |
| `workflow` | Process improvements |
| `tooling` | Tool usage tips |

## Searching Learnings

Before starting work, check what others learned:

```python
roboco_search_learnings(
    query="database connection pooling",
    category="performance",  # Optional filter
    team="backend",          # Optional filter
    top_k=10
)
```

## What to Record

**DO record:**
- Solutions to tricky problems
- Performance optimizations discovered
- Security patterns you implemented
- Testing strategies that worked
- Workflow improvements
- Tool configurations that helped

**DON'T record:**
- Obvious/basic knowledge
- Temporary workarounds
- Context-specific hacks
- Personal preferences

## Good Learning Examples

```python
# Specific and actionable
roboco_record_learning(
    content="Redis SCAN is O(N) total but O(1) per call. Use SCAN over KEYS for large datasets.",
    category="performance",
    tags=["redis", "scan", "keys"]
)

# Pattern with context
roboco_record_learning(
    content="Use circuit breakers for external API calls. Implemented in services/http_client.py:42",
    category="pattern",
    tags=["resilience", "circuit-breaker", "api"]
)

# Security insight
roboco_record_learning(
    content="Always validate file uploads server-side. Client validation is insufficient.",
    category="security",
    tags=["upload", "validation"]
)
```

## Learning Flow

```
Agent solves problem
        |
        v
roboco_record_learning()
        |
        v
+------------------+
| Indexed in KB    |
+------------------+
        |
        +-- Available via roboco_search_learnings()
        +-- Included in roboco_ask_mentor() responses
        +-- Injected in proactive context for similar tasks
        |
        v
Future agents benefit
```

## Best Practices

1. **Record immediately** - Don't wait, you'll forget details
2. **Be specific** - Include file paths, function names
3. **Add context** - Why does this matter?
4. **Tag appropriately** - Helps future discovery
5. **Search first** - Before solving, check if someone already did

## Team vs Org Scope

- `team` filter: Learnings from your cell (backend/frontend/ux_ui)
- No filter: Learnings from entire organization

Cross-team learnings are often valuable - security and performance insights apply everywhere.
