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
    project="roboco-api",
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

## Documentation Writing (Documenter, Cell PM)

```python
# Write/update documentation (auto-dedup via RAG)
roboco_docs_write({
    "task_id": "task-uuid",
    "filename": "api-endpoints.md",
    "doc_type": "api",  # api, qa, guide, readme, changelog, architecture, design
    "title": "API Endpoints",
    "content": "# API Endpoints\n\n..."
})

# List docs for a task
roboco_docs_list(task_id="task-uuid")

# Read a doc
roboco_docs_read(path="backend/api/endpoints.md")
```

**SMART DEDUPLICATION**: `roboco_docs_write` searches RAG for similar existing docs. If high-similarity match found, updates instead of creating duplicate.

## Bulk Indexing

```python
# Index code (PM, Developer)
roboco_kb_index_code(
    sources=["src/**/*.py"],
    project="roboco-api"
)

# Index docs (PM, Documenter) - for bulk/explicit indexing
# Note: roboco_docs_write() auto-indexes when writing
roboco_kb_index_docs(
    sources=["docs/**/*.md"],
    project="roboco-api"
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

## Standards & Validation

### Get Standards

```python
roboco_get_standards(domain="coding", language="python")
```

**Domains:** `coding`, `security`, `workflow`, `architecture`

### Validate Action (LLM-Based)

Uses LLM to check code/context against organizational standards.

```python
result = roboco_validate_action(
    action_type="create_endpoint",
    context="""
def create_user(email, password):
    user = User(email=email, password=password)
    db.add(user)
    return user
"""
)
```

**Returns:**

```json
{
  "allowed": false,
  "violations": [
    {
      "rule_id": "SEC-001",
      "rule_title": "Password Hashing",
      "message": "Password stored in plaintext",
      "severity": "error",
      "suggestion": "Hash password with bcrypt before storage"
    }
  ],
  "warnings": [...],
  "relevant_standards": [...]
}
```

**How it works:**
1. Searches KB for relevant standards based on `action_type`
2. Sends standards + context to LLM for analysis
3. Returns structured violations with fix suggestions
4. Falls back to heuristic matching if LLM unavailable

**Action types:** `create_endpoint`, `add_dependency`, `database_migration`, `auth_change`, `file_upload`, `external_api`

### Code Review

```python
roboco_review_code(
    code="def handle(...):",
    file_path="src/api/auth.py",
    change_type="modify"  # add, modify, delete
)
```

**Returns:** Score (0-100), comments by severity, approval status
