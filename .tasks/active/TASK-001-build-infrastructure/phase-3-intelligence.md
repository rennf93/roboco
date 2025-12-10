# TASK-001: Phase 3 - Intelligence Services

## Status
- **State**: completed
- **Priority**: P0
- **Cell**: board

## Dates
- **Created**: 2025-12-09
- **Completed**: 2025-12-09

## Overview
Implement Phase 3 of the RoboCo system per HOMELAB_TEAM_V0.md blueprint (Section 13.5):
- RAG system using piragi with PostgreSQL/pgvector
- Optimal API for knowledge base queries
- Journal API for agent personal logs
- Embedding pipeline integration

## Acceptance Criteria
- [x] pgvector enabled in PostgreSQL (docker-compose updated)
- [x] piragi[postgres] added as dependency
- [x] Optimal API service with piragi
- [x] Journal API service
- [x] API routes for knowledge base operations
- [x] API routes for journal operations
- [x] Integration with existing extraction pipeline

## What Was Built

### 1. Infrastructure Changes

#### Docker Compose (`docker-compose.yml`)
- Changed PostgreSQL image from `postgres:16-alpine` to `pgvector/pgvector:pg16`
- Removed Qdrant container (replaced by pgvector)
- Removed `qdrant_data` volume

#### Dependencies (`pyproject.toml`)
- Replaced `qdrant-client>=1.7.0` with `piragi[postgres]>=0.1.0`
- Updated mypy overrides for piragi

### 2. Configuration (`roboco/config.py`)
| Setting | Default | Description |
|---------|---------|-------------|
| `rag_persist_dir` | `.piragi` | Directory for piragi index data |
| `rag_chunk_strategy` | `semantic` | Chunking: fixed, semantic, hierarchical, contextual |
| `rag_chunk_size` | 512 | Characters per chunk |
| `rag_chunk_overlap` | 50 | Overlap between chunks |
| `rag_use_hyde` | True | Hypothetical document embeddings |
| `rag_use_hybrid_search` | True | BM25 + vector hybrid search |
| `rag_use_cross_encoder` | False | Neural reranking (slower) |
| `rag_auto_update_enabled` | True | Background index updates |
| `rag_auto_update_interval` | 300 | Seconds between updates |
| `rag_store_url` | computed | PostgreSQL connection for piragi |

### 3. Optimal API Service (`roboco/services/optimal.py`)

| Component | Description |
|-----------|-------------|
| `IndexType` | Enum: CODE, DOCUMENTATION, CONVERSATIONS, JOURNALS |
| `SearchResult` | Single search result with content, source, score |
| `RAGResponse` | Answer with citations |
| `QueryContext` | Filters: project, task_id, agent_id, index_types |
| `OptimalService` | Main service using AsyncRagi |

Key features:
- Multiple indexes for different content types
- Async initialization and cleanup
- Code indexing (files, directories, globs)
- Documentation indexing (markdown, URLs, crawling)
- Conversation indexing (from extraction pipeline)
- Journal entry indexing (automatic on creation)
- Semantic search across all indexes
- RAG queries with citations
- HyDE and hybrid search enabled by default

### 4. Journal API Service (`roboco/services/journal.py`)

| Component | Description |
|-----------|-------------|
| `JournalStats` | Entry counts, timestamps, summary status |
| `GrowthMetrics` | Learning frequency, resolution rates, trends |
| `JournalService` | Full CRUD for journals and entries |

Key features:
- Get or create journal per agent
- Create entries with automatic RAG indexing
- Convenience methods for entry types:
  - Task reflections
  - Decision logs
  - Learning entries
  - Struggle entries
  - General notes
- List entries with filtering (type, task, privacy)
- Journal statistics
- Growth metrics calculation
- Semantic search through entries

### 5. API Routes

#### Optimal API (`roboco/api/routes/optimal.py`)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/optimal/kb/index/code` | POST | Index code files |
| `/api/v1/optimal/kb/index/docs` | POST | Index documentation |
| `/api/v1/optimal/kb/search` | POST | Semantic search |
| `/api/v1/optimal/kb/similar` | GET | Find similar documents |
| `/api/v1/optimal/rag/query` | POST | RAG query with answer |
| `/api/v1/optimal/rag/context` | POST | Get context without answer |
| `/api/v1/optimal/stats` | GET | Index statistics |
| `/api/v1/optimal/kb/{type}` | DELETE | Clear an index |
| `/api/v1/optimal/kb/refresh` | POST | Refresh index sources |

#### Journal API (`roboco/api/routes/journals.py`)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/journals/me` | GET | Get my journal |
| `/api/v1/journals/{agent_id}` | GET | Get journal by agent |
| `/api/v1/journals/me/entries` | GET | List my entries |
| `/api/v1/journals/me/entries` | POST | Create entry |
| `/api/v1/journals/entries/{id}` | GET | Get entry |
| `/api/v1/journals/entries/{id}` | DELETE | Delete entry |
| `/api/v1/journals/me/reflections` | POST | Add task reflection |
| `/api/v1/journals/me/decisions` | POST | Add decision log |
| `/api/v1/journals/me/learnings` | POST | Add learning |
| `/api/v1/journals/me/struggles` | POST | Add struggle |
| `/api/v1/journals/me/notes` | POST | Add general note |
| `/api/v1/journals/me/stats` | GET | Get journal stats |
| `/api/v1/journals/me/growth` | GET | Get growth metrics |
| `/api/v1/journals/me/search` | POST | Semantic search entries |

### 6. App Integration (`roboco/api/app.py`)
- OptimalService initialized in lifespan
- Stored in `app.state.optimal`
- Proper cleanup on shutdown

## File Structure
```
roboco/services/
├── __init__.py           # Updated with Phase 3 exports
├── transcription.py      # Phase 2
├── extraction.py         # Phase 2
├── permissions.py        # Phase 2
├── optimal.py            # NEW - RAG/Knowledge Base
└── journal.py            # NEW - Agent Journals

roboco/api/routes/
├── __init__.py           # Updated with new routes
├── ... (Phase 1-2 routes)
├── optimal.py            # NEW - Optimal API endpoints
└── journals.py           # NEW - Journal API endpoints
```

## Technology Choice: piragi

Selected piragi over Qdrant because:
1. **Simpler stack** - Uses existing PostgreSQL with pgvector extension
2. **Zero config** - Works with local models out of the box
3. **Advanced retrieval** - Built-in HyDE, hybrid search, cross-encoder reranking
4. **Async support** - AsyncRagi for FastAPI integration
5. **Multiple sources** - Files, directories, URLs, globs, web crawling
6. **Auto-updates** - Background refresh without blocking queries

## Next Steps (Phase 4: Agents)
Per HOMELAB_TEAM_V0.md section 13.6:
- [ ] Define agent prompts per role
- [ ] Implement Dev workflow
- [ ] Implement QA workflow
- [ ] Implement Documenter workflow
- [ ] Implement PM workflows
- [ ] Deploy Backend cell
- [ ] Deploy Frontend cell
- [ ] Deploy UX/UI cell

## Quick Context Restore
Phase 3 intelligence services complete. Using piragi with pgvector for RAG instead of Qdrant - simpler stack, same functionality. Optimal API provides knowledge base indexing and queries. Journal API provides agent personal logs with automatic RAG indexing. All entries are searchable via semantic search. Ready for Phase 4 which implements the actual agent workflows.
