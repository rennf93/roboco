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
# Search for similar errors FIRST
roboco_search_error(
    error_message="Redis connection timed out",
    context="trying to connect during startup"
)

# Record an error and how you fixed it
roboco_record_error_solution(
    error_message="Redis connection timed out",
    context="Service startup - Redis wasn't ready yet",
    solution="Added retry logic with exponential backoff",
    worked=True,
    tags=["redis", "startup", "timeout"]
)
```

---

## Decision Tracking

Record architectural decisions:

```python
# Check if similar decisions exist FIRST
roboco_check_decision(topic="session storage")
# Returns: has_precedent, decisions, recommendation

# Record a decision
roboco_record_decision(params={
    "topic": "Database for session storage",
    "decision": "Use Redis instead of PostgreSQL",
    "rationale": "Need sub-millisecond reads, sessions are ephemeral",
    "alternatives": [
        {"name": "PostgreSQL", "pros": "ACID", "cons": "Too slow"},
        {"name": "In-memory", "pros": "Fast", "cons": "No persistence"}
    ],
    "scope": "team",  # or "org"
    "tags": ["database", "session", "architecture"]
})
```

---

## Standards Validation

Check code against team standards:

```python
# Get applicable standards for a domain
roboco_get_standards(
    domain="coding",    # or "security", "workflow"
    language="python"   # optional filter
)

# Validate an action against standards
roboco_validate_action(
    action_type="create_endpoint",
    context="Adding user management API endpoint"
)
# Returns: allowed, violations, warnings, relevant_standards

# Get code reviewed before committing
roboco_review_code(
    code="def handle_auth(token): ...",
    file_path="src/api/auth.py",
    change_type="modify"  # or "add", "delete"
)
# Returns: approved, score (0-100), comments, standards_checked
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

The system automatically provides relevant context when you claim a task:

```python
# Get context that was injected when task was claimed
roboco_get_proactive_context(
    task_id="uuid-here",
    force_refresh=False  # True to regenerate fresh context
)

# Returns:
# - similar_tasks: Past tasks like this one
# - relevant_learnings: What others learned doing similar work
# - code_patterns: Relevant code examples
# - applicable_standards: Standards that apply
# - recent_decisions: Related architectural decisions
# - known_issues: Issues you should be aware of
# - summary: Human-readable overview
```

This helps you start informed without manual searching.

---

## Mentor (Conversational RAG)

Ask the organizational knowledge base for help with follow-up context:

```python
# First question
response = roboco_ask_mentor(
    question="How do I handle authentication in this codebase?",
    domain="coding"  # optional: coding, security, workflow
)

# Follow-up question (maintains conversation context)
roboco_ask_mentor(
    question="What about refresh tokens?",
    conversation_id=response["conversation_id"]
)

# Returns: answer, sources, suggested_followups
```

The mentor searches across standards, decisions, learnings, and code patterns.

---

## Index Management

### Check Index Health

```python
roboco_index_status()
# Returns: initialized, indexes with document_count, chunk_count, last_updated
```

### Trigger Reindexing (PM/Developer)

```python
roboco_reindex_all(force=False)
# force=True reindexes even if indexes aren't empty
# Returns: code_files_indexed, docs_files_indexed
```

### Clear an Index (PM only)

```python
roboco_clear_index(index_type="code")
# Valid types: code, documentation, conversations, journals,
#              errors, standards, decisions, reviews, learnings
```

---

## Lifecycle Tracking

Task lifecycle events are automatically indexed for pattern analysis:

| Event | What's Tracked |
|-------|----------------|
| `block` | Which task blocked, blocker title |
| `unblock` | When unblocked |
| `pause` | When paused |
| `resume` | When resumed |
| `cancel` | Who cancelled, how many descendants cancelled |

This enables queries like:
- "Which tasks get cancelled most often?"
- "What causes the most blocks?"
- "Which teams have the longest pause durations?"

---

## Tool Quick Reference

### Core Search & Query

| Tool | Purpose | Who Can Use |
|------|---------|-------------|
| `roboco_kb_search` | Semantic search across all indexes | Everyone |
| `roboco_rag_query` | AI-generated answers with citations | Everyone |
| `roboco_kb_stats` | What's indexed (counts by type) | Everyone |
| `roboco_tokens_estimate` | Estimate token count for content | Everyone |

### Indexing & Management

| Tool | Purpose | Who Can Use |
|------|---------|-------------|
| `roboco_kb_index_code` | Index code files | PM, Developer |
| `roboco_kb_index_docs` | Index documentation | PM, Documenter |
| `roboco_clear_index` | Clear a specific index | PM |
| `roboco_reindex_all` | Trigger full code+docs reindex | PM, Developer |
| `roboco_index_status` | Detailed index health & counts | Everyone |

### Mentor (Conversational RAG)

| Tool | Purpose | Who Can Use |
|------|---------|-------------|
| `roboco_ask_mentor` | Conversational help with follow-ups | Everyone |

### Error Tracking

| Tool | Purpose | Who Can Use |
|------|---------|-------------|
| `roboco_search_error` | Find past error solutions | Everyone |
| `roboco_record_error_solution` | Record how you fixed an error | Everyone |

### Decision Tracking

| Tool | Purpose | Who Can Use |
|------|---------|-------------|
| `roboco_check_decision` | Check for similar past decisions | Everyone |
| `roboco_record_decision` | Record an architectural decision | Everyone |

### Standards & Validation

| Tool | Purpose | Who Can Use |
|------|---------|-------------|
| `roboco_get_standards` | Get applicable standards | Everyone |
| `roboco_validate_action` | Validate action against standards | Everyone |
| `roboco_review_code` | AI-assisted code review | Developer, QA |

### Learning & Knowledge Sharing

| Tool | Purpose | Who Can Use |
|------|---------|-------------|
| `roboco_record_learning` | Record a learning for future agents | Everyone |
| `roboco_search_learnings` | Search learnings from teammates | Everyone |

### Proactive Context

| Tool | Purpose | Who Can Use |
|------|---------|-------------|
| `roboco_get_proactive_context` | Get context injected at task claim | Everyone |

### Journal Tools

| Tool | Purpose | Who Can Use |
|------|---------|-------------|
| `roboco_journal_search` | Search your journal | Everyone |
| `roboco_journal_read_team` | Read team journals | PM, Documenter |
