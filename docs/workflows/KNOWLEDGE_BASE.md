# Knowledge Base Guide

## Overview

The knowledge base is built from **9 specialized indexes**:

| Index Type | Content | Use Case |
|------------|---------|----------|
| **code** | Source files | Find implementations, patterns |
| **docs** | Documentation, READMEs | Find guides, specs |
| **conversations** | Channel discussions | Find past discussions |
| **journals** | Agent journal entries | Find decisions, learnings |
| **errors** | Error patterns & fixes | Find solutions to past errors |
| **standards** | Coding standards, rules | Validate against standards |
| **decisions** | Architectural decisions | Find past design choices |
| **reviews** | Code review patterns | Find review templates |
| **learnings** | Captured learnings | Find team knowledge |

All content is **embedded** (vectorized) for semantic search.

**Document Tracking:** The system tracks actual documents indexed (not just vector chunks), including source path, title, preview, and chunk count.

---

## Knowledge Base Tools

### Semantic Search

```python
roboco_kb_search(
    query="rate limiting redis implementation",
    top_k=5,                    # Results to return (1-20)
    project="roboco",           # Optional project filter
    task_id="uuid-here",        # Optional task filter
    index_types=["code", "docs"]  # Filter by type
)
```

Returns semantically similar content from indexed code, docs, and learnings.

### RAG Queries (AI-Generated Answers)

```python
roboco_rag_query(
    query="How does authentication work in this codebase?",
    top_k=5,                    # Context chunks to use
    project="roboco"            # Optional project filter
)
```

Returns an AI-synthesized answer with citations to sources.

**Good for questions like:**
- "How does authentication work?"
- "What pattern should I use for error handling?"
- "What decisions were made about the database schema?"

### Check What's Indexed

```python
roboco_kb_stats()
# Returns: indexed content counts by type
```

### Estimate Token Count

```python
roboco_tokens_estimate(content="...", model="claude-sonnet-4")
# Returns: token count for context planning
```

---

## Indexing Content (PM/Developer/Documenter)

### Index Code (PM, Developer)

```python
roboco_kb_index_code(
    sources=["src/**/*.py", "lib/**/*.ts"],
    project="roboco"
)
```

### Index Documentation (PM, Documenter)

```python
roboco_kb_index_docs(
    sources=["docs/**/*.md", "README.md"],
    project="roboco"
)
```

---

## Error Tracking

Record and search error patterns:

```python
# Record an error and how you fixed it
roboco_record_error(
    error_type="ConnectionError",
    message="Redis connection timed out",
    solution="Increased timeout to 30s and added retry logic",
    worked=True
)

# Search for similar errors
roboco_search_error(
    pattern="ConnectionError",
    context="redis timeout"
)
```

---

## Decision Tracking

Record architectural decisions:

```python
# Record a decision
roboco_record_decision(
    topic="Database for session storage",
    decision="Use Redis instead of PostgreSQL",
    rationale="Need sub-millisecond reads, sessions are ephemeral",
    alternatives=["PostgreSQL", "In-memory"],
    task_id="uuid-here"
)

# Check if similar decisions exist
roboco_decision_check(
    topic="session storage",
    proposed_approach="Use in-memory cache"
)
# Returns: relevant past decisions to consider
```

---

## Standards Validation

Check code against team standards:

```python
# Get applicable standards for a file
roboco_standards_get(
    file_path="src/api/routes/users.py",
    domain="api"
)

# Validate an action against standards
roboco_validate_action(
    action="Adding a new API endpoint",
    context="User management feature"
)
```

---

## Learning Capture

Record and share learnings:

```python
# Record a learning
roboco_record_learning(
    content="Redis SCAN is better than KEYS for large datasets",
    category="performance",
    shareable=True,
    tags=["redis", "performance", "patterns"]
)

# Search learnings
roboco_kb_search(
    query="redis performance patterns",
    index_types=["learnings"]
)
```

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

## Proactive Context

The system can automatically provide relevant context when you claim a task:

```python
# Automatic context injection on task claim
# System searches KB for:
# - Similar past tasks
# - Related decisions
# - Relevant standards
# - Past error solutions
```

This helps you start informed without manual searching.

---

## Code Review Support

Request AI-assisted code review:

```python
roboco_code_review(
    file_path="src/api/routes/users.py",
    focus=["security", "performance"]
)
# Returns: review comments, standards checked, similar past reviews
```

---

## Tool Quick Reference

| Tool | Purpose | Who Can Use |
|------|---------|-------------|
| `roboco_kb_search` | Semantic search | Everyone |
| `roboco_rag_query` | AI-generated answers | Everyone |
| `roboco_kb_stats` | What's indexed | Everyone |
| `roboco_kb_index_code` | Index code files | PM, Developer |
| `roboco_kb_index_docs` | Index documentation | PM, Documenter |
| `roboco_tokens_estimate` | Token count | Everyone |
| `roboco_journal_search` | Search your journal | Everyone |
| `roboco_journal_read_team` | Read team journals | PM, Documenter |
| `roboco_record_error` | Record error & fix | Everyone |
| `roboco_search_error` | Find past errors | Everyone |
| `roboco_record_decision` | Record decision | Everyone |
| `roboco_decision_check` | Check past decisions | Everyone |
| `roboco_standards_get` | Get applicable standards | Everyone |
| `roboco_validate_action` | Validate against standards | Everyone |
| `roboco_record_learning` | Record a learning | Everyone |
| `roboco_code_review` | AI-assisted review | Developer, QA |
