# Optimal Brain Architecture

The Optimal Brain is RoboCo's organizational intelligence layer - a knowledge system that enables agents to learn from each other, enforce standards, and make consistent decisions.

## Overview

```
+-----------------------------------------------------------------------+
|                           OPTIMAL BRAIN                                |
|                                                                        |
|  +-------------+  +-------------+  +-------------+  +-------------+   |
|  |   Mentor    |  |   Error     |  |  Decision   |  |  Standards  |   |
|  |   System    |  |  Patterns   |  |   Memory    |  |  Enforcer   |   |
|  +-------------+  +-------------+  +-------------+  +-------------+   |
|  +-------------+  +-------------+  +-------------+                    |
|  |  Learning   |  |  Proactive  |  |    Code     |                    |
|  |   Network   |  |   Context   |  |   Review    |                    |
|  +-------------+  +-------------+  +-------------+                    |
|         |               |               |               |              |
|         +---------------+---------------+---------------+              |
|                                 |                                      |
|                    +------------------------+                          |
|                    |   Piragi + pgvector    |                          |
|                    |   (PostgreSQL)         |                          |
|                    +------------------------+                          |
+-----------------------------------------------------------------------+
```

## Index Types

| Index | Content | Auto-Updated |
|-------|---------|--------------|
| `code` | Source files | On commit |
| `docs` | Documentation | On write |
| `decisions` | Architectural choices | Manual |
| `errors` | Error patterns + solutions | Manual |
| `standards` | Coding/security/workflow rules | On boot |
| `learnings` | Agent insights | Manual |
| `reviews` | Code review patterns | On review |
| `conversations` | Channel discussions | On message |
| `journals` | Agent reflections | On entry |

## Components

### 1. Mentor System

Conversational RAG for agent questions. Maintains context across follow-ups.

**Tool:** `roboco_ask_mentor`

```python
# First question
response = roboco_ask_mentor(question="How do I handle auth?")

# Follow-up (uses conversation context)
roboco_ask_mentor(
    question="What about refresh tokens?",
    conversation_id=response["conversation_id"]
)
```

**Searches:** standards, decisions, learnings, code, errors

### 2. Error Pattern Database

Collective error memory. When one agent solves an error, all agents benefit.

**Tools:** `roboco_search_error`, `roboco_record_error_solution`

```python
# Before debugging
roboco_search_error(error_message="Redis timeout", context="startup")

# After fixing
roboco_record_error_solution(
    error_message="Redis timeout",
    solution="Added retry with exponential backoff",
    worked=True
)
```

### 3. Decision Memory

Prevents inconsistent architectural choices. Check before deciding.

**Tools:** `roboco_check_decision`, `roboco_record_decision`

```python
# Before deciding
roboco_check_decision(topic="session storage")

# After deciding
roboco_record_decision(params={
    "topic": "Session storage",
    "decision": "Use Redis",
    "rationale": "Sub-ms reads, existing infra"
})
```

### 4. Standards Enforcer

Pre-action validation against organizational rules.

**Tools:** `roboco_get_standards`, `roboco_validate_action`, `roboco_review_code`

```python
# Before writing code
roboco_get_standards(domain="coding", language="python")

# Validate action
roboco_validate_action(
    action_type="create_endpoint",
    context="Adding /users POST endpoint"
)

# Review code
roboco_review_code(code="...", file_path="api/users.py")
```

### 5. Learning Network

Cross-agent knowledge sharing. Learnings propagate organization-wide.

**Tools:** `roboco_record_learning`, `roboco_search_learnings`

```python
# Record insight
roboco_record_learning(
    content="Use transactions for multi-table updates",
    category="pattern",
    shareable=True
)

# Search learnings
roboco_search_learnings(query="database transactions")
```

### 6. Proactive Context

Auto-injected knowledge when agents claim tasks.

**Tool:** `roboco_get_proactive_context`

Returns:
- Similar completed tasks
- Relevant learnings
- Applicable standards
- Recent decisions
- Known issues
- Code patterns

## Data Flow

### Task Claim Flow

```
Agent claims task
       |
       v
+-------------------+
| ProactiveContext  |
| Service           |
+-------------------+
       |
       +-- Search similar tasks (completed)
       +-- Search relevant learnings
       +-- Get applicable standards
       +-- Get recent decisions
       +-- Search known issues
       |
       v
Context injected into task.proactive_context
       |
       v
Agent receives context on task start
```

### Learning Flow

```
Agent discovers insight
       |
       v
roboco_record_learning()
       |
       v
+-------------------+
| OptimalService    |
+-------------------+
       |
       +-- Store in PostgreSQL
       +-- Index in pgvector (learnings index)
       +-- Optionally notify similar-role agents
       |
       v
Future agents find via search
```

## MCP Server

**Location:** `roboco/mcp/optimal_server.py`

**Tool Groups:**

| Group | Tools |
|-------|-------|
| Search | `kb_search`, `rag_query`, `kb_stats` |
| Indexing | `kb_index_code`, `kb_index_docs` |
| Mentor | `ask_mentor` |
| Errors | `search_error`, `record_error_solution` |
| Decisions | `check_decision`, `record_decision` |
| Standards | `get_standards`, `validate_action`, `review_code` |
| Learning | `record_learning`, `search_learnings` |
| Context | `get_proactive_context` |
| Admin | `clear_index`, `reindex_all`, `index_status` |

## API Endpoints

**Location:** `roboco/api/routes/optimal.py`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/optimal/kb/search` | POST | Semantic search |
| `/optimal/rag/query` | POST | RAG answer |
| `/optimal/mentor/ask` | POST | Conversational help |
| `/optimal/errors/search` | POST | Error lookup |
| `/optimal/errors/record` | POST | Record solution |
| `/optimal/decisions/check` | POST | Check precedent |
| `/optimal/decisions/record` | POST | Record decision |
| `/optimal/standards/get` | POST | Get standards |
| `/optimal/standards/validate` | POST | Validate action |
| `/optimal/review/code` | POST | Code review |
| `/optimal/learnings/record` | POST | Record learning |
| `/optimal/learnings/search` | POST | Search learnings |
| `/optimal/context/proactive` | POST | Get context |
| `/optimal/stats` | GET | Index stats |

## Configuration

```bash
# Embedding model (local)
ROBOCO_DEFAULT_EMBEDDING_MODEL=qwen3-embedding:0.6b

# LLM for RAG synthesis
ROBOCO_LOCAL_LLM_MODEL=glm-4.7:cloud
ROBOCO_LOCAL_LLM_BASE_URL=http://roboco-ollama:11434/v1

# RAG settings
ROBOCO_RAG_CHUNK_STRATEGY=fixed
ROBOCO_RAG_CHUNK_SIZE=512
ROBOCO_RAG_USE_HYDE=true
ROBOCO_RAG_USE_HYBRID_SEARCH=true
```

## Best Practices

1. **Ask mentor first** - `roboco_ask_mentor` is the primary tool
2. **Check before deciding** - Use `roboco_check_decision`
3. **Record solutions** - Use `roboco_record_error_solution`
4. **Share learnings** - Use `roboco_record_learning`
5. **Validate actions** - Use `roboco_validate_action`
