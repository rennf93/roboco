# Knowledge Base Tools

## Search and Query

| Tool | Purpose |
|------|---------|
| `roboco_kb_search` | Semantic search |
| `roboco_rag_query` | AI-synthesized answer |
| `roboco_ask_mentor` | Conversational help |
| `roboco_kb_stats` | Index statistics |

## Semantic Search

```python
roboco_kb_search(
    query="rate limiting redis",
    top_k=5,
    project="roboco",
    index_types=["code", "docs"]
)
```

## AI-Generated Answers

```python
roboco_rag_query(
    query="How does authentication work?",
    top_k=5
)
```

## Mentor (Conversational)

```python
response = roboco_ask_mentor(
    question="How do I handle auth?",
    domain="coding"
)

# Follow-up
roboco_ask_mentor(
    question="What about refresh tokens?",
    conversation_id=response["conversation_id"]
)
```

## Indexing

```python
# Index code (PM, Developer)
roboco_kb_index_code(
    sources=["src/**/*.py"],
    project="roboco"
)

# Index docs (PM, Documenter)
roboco_kb_index_docs(
    sources=["docs/**/*.md"],
    project="roboco"
)
```

## Error Tracking

```python
# Search for similar errors
roboco_search_error(
    error_message="Redis connection timed out",
    context="startup"
)

# Record solution
roboco_record_error_solution(
    error_message="Redis connection timed out",
    solution="Added retry with backoff",
    worked=True
)
```

## Decision Tracking

```python
# Check for similar decisions
roboco_check_decision(topic="session storage")

# Record decision
roboco_record_decision(params={
    topic: "Session storage",
    decision: "Use Redis",
    rationale: "Sub-ms reads"
})
```

## Standards

```python
# Get standards
roboco_get_standards(domain="coding", language="python")

# Validate action
roboco_validate_action(
    action_type="create_endpoint",
    context="Adding user API"
)

# Code review
roboco_review_code(
    code="def handle(...):",
    file_path="src/api/auth.py"
)
```
