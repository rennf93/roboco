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
    index_types=["code", "docs"],
)
```

## AI-Generated Answers

```python
roboco_rag_query(query="How does authentication work?", top_k=5)
```

Both `roboco_kb_search` and `roboco_rag_query` fan out across every registered index concurrently with a **per-index 15s timeout** (inner to the route's 30s outer bound). If an index does not finish, the others still return their results and the response carries a `gaps` list naming the timed-out indexes (e.g. `["journals unavailable: timed out after 15s"]`), empty when all indexes completed. A slow index no longer discards the whole result set.

When `gaps` is non-empty, the MCP tool response includes the `gaps` list and a `hint` string: `"Partial results: N index(es) timed out (name1, ...). Results may be incomplete."` (or "Answer may be incomplete" for `roboco_rag_query`). When there are no gaps, both are omitted — the response is identical to the pre-change shape. The "No results / try mentor" hint is suppressed on a partial-results response so it isn't mistaken for a no-match query.

At the HTTP level, a **total outage** (every index timed out, no results returned) returns a 504 error envelope, not HTTP 200 — partial degradation never masks a total outage. A search that completes with zero results and no gaps is still a 200 (a legitimate "nothing matched", not a failure).

See `docs/backend/services/optimal-per-index-timeout.md` for the service contract and `docs/backend/api/optimal-gaps-surface.md` for the route/schema/ MCP surface.

## Mentor (Conversational)

```python
response = roboco_ask_mentor(question="How do I handle auth?", domain="coding")

# Follow-up
roboco_ask_mentor(
    question="What about refresh tokens?", conversation_id=response["conversation_id"]
)
```

## Documentation Writing (Documenter, Cell PM)

```python
# Write/update documentation (auto-dedup via RAG)
roboco_docs_write(
    {
        "task_id": "task-uuid",
        "filename": "api-endpoints.md",
        "doc_type": "api",  # api, qa, guide, readme, changelog, architecture, design
        "title": "API Endpoints",
        "content": "# API Endpoints\n\n...",
    }
)

# List docs for a task
roboco_docs_list(task_id="task-uuid")

# Read a doc
roboco_docs_read(path="backend/api/endpoints.md")
```

**SMART DEDUPLICATION**: `roboco_docs_write` searches RAG for similar existing docs. If high-similarity match found, updates instead of creating duplicate.

**LIVE-WRITE PROVENANCE**: a doc indexed via `roboco_docs_write` (or captured from your workspace at `i_documented`) is written mid-task, before your task's PR merges — it may describe an API/contract that doesn't exist yet on the deployed tree. It's indexed with `provenance: "live_write"`, and any `roboco_kb_search` / `roboco_ask_mentor` / `roboco_rag_query` hit built from it comes back with an appended line: `[caveat: written during in-flight work — verify the contract exists on the deployed tree/git before relying on it]`. Docs picked up by the repo-tree scan (`docs/rag`, `docs/map`, or a manual/startup reindex) carry `provenance: "repo_tree"` instead and render with no caveat.

**The caveat is temporary now.** When the writing task's ROOT chain reaches terminal `completed` (PR merge precedes completion in this system, so this fires after the merge), `TaskService`'s completion hook flips those chunks' provenance to `provenance: "repo_tree"` in place — queried by the stamped `task_id`, no reindex — and the caveat disappears from every subsequent KB hit. The guard is the root chain: a leaf or cell task completing while its root is still in flight does NOT flip, so a caveat outlives its own task but never outlives the merge to master. Before the flip lands, the marker persists even after merge — so a stale caveat still reads as "verify against git", not "this is unmerged". See `docs/backend/services/kb-provenance-flip.md` for the full lifecycle.

## Bulk Indexing

```python
# Index code (PM, Developer)
roboco_kb_index_code(sources=["src/**/*.py"], project="roboco-api")

# Index docs (PM, Documenter) - for bulk/explicit indexing
# Note: roboco_docs_write() auto-indexes when writing
roboco_kb_index_docs(sources=["docs/**/*.md"], project="roboco-api")
```

## Error Tracking

```python
# Search for similar errors
roboco_search_error(error_message="Redis connection timed out", context="startup")

# Record solution
roboco_record_error_solution(
    error_message="Redis connection timed out",
    solution="Added retry with backoff",
    worked=True,
)
```

## Decision Tracking

```python
# Check for similar decisions
roboco_check_decision(topic="session storage")

# Record decision
roboco_record_decision(
    params={topic: "Session storage", decision: "Use Redis", rationale: "Sub-ms reads"}
)
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
""",
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
    change_type="modify",  # add, modify, delete
)
```

**Returns:** Score (0-100), comments by severity, approval status
