# Knowledge Base Search

## Search Types

| Tool | Purpose |
|------|---------|
| `roboco_kb_search` | Semantic search across indexes |
| `roboco_rag_query` | AI-synthesized answer |
| `roboco_ask_mentor` | Conversational help |

## Semantic Search

```python
roboco_kb_search(
    query="rate limiting redis implementation",
    top_k=5,                      # Results to return
    project="roboco",             # Optional project filter
    index_types=["code", "docs"]  # Filter by type
)
```

Returns similar content - not just keyword matches.

## RAG Query (AI Answer)

```python
roboco_rag_query(
    query="How does authentication work in this codebase?",
    top_k=5
)
```

Returns AI-synthesized answer with citations.

Good for:
- "How does X work?"
- "What pattern should I use?"
- "What decisions were made about Y?"

## Mentor (Conversational)

```python
# First question
response = roboco_ask_mentor(
    question="How do I handle authentication?",
    domain="coding"
)

# Follow-up
roboco_ask_mentor(
    question="What about refresh tokens?",
    conversation_id=response["conversation_id"]
)
```

## Index Types

| Type | Content |
|------|---------|
| `code` | Source files |
| `docs` | Documentation |
| `conversations` | Channel discussions |
| `journals` | Agent journal entries |
| `errors` | Error patterns & fixes |
| `standards` | Coding rules |
| `decisions` | Architectural decisions |
| `reviews` | Code review patterns |
| `learnings` | Captured learnings |

## Before Starting a Task

Always search first:
```python
roboco_kb_search("implementing rate limiter")
roboco_journal_search("rate limit decisions")
```

This helps you:
- Avoid repeating mistakes
- Find proven patterns
- Learn from others' experiences

## Proactive Context

System auto-provides context when you claim:
```python
roboco_get_proactive_context(task_id)
# Returns: similar_tasks, relevant_learnings, code_patterns,
#          applicable_standards, recent_decisions, known_issues
```
