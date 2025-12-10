# TASK-002: Phase 2 - Communication Services

## Status
- **State**: completed
- **Priority**: P0
- **Cell**: board

## Dates
- **Created**: 2025-12-09
- **Completed**: 2025-12-09

## Overview
Implement Phase 2 of the RoboCo system per HOMELAB_TEAM_V0.md blueprint (Section 13.4):
- Transcription service (process LLM stream output)
- Message extraction pipeline (identify message types)
- Permission system (communication matrix enforcement)

## Acceptance Criteria
- [x] Transcription service buffers LLM stream chunks
- [x] Extraction pipeline classifies messages (reasoning, dialogue, decision, action, blocker, technical)
- [x] Permission service enforces channel read/write access
- [x] Permission service enforces notification permissions
- [x] Permission service enforces communication matrix
- [x] API routes for stream processing
- [x] API routes for permission checks

## What Was Built

### 1. Transcription Service (`roboco/services/transcription.py`)
| Component | Description |
|-----------|-------------|
| `StreamBuffer` | Accumulates chunks from agent, tracks timing, detects readiness |
| `TranscriptionConfig` | Configurable thresholds (min chars, max chars, idle timeout) |
| `TranscriptionService` | Main service with lifecycle, buffering, periodic flush |

Key features:
- Buffers raw LLM output by agent/session
- Detects segment boundaries (sentences, pauses, max length)
- Background task for periodic buffer checking
- Callback registration for ready segments

### 2. Extraction Service (`roboco/services/extraction.py`)
| Component | Description |
|-----------|-------------|
| Pattern matchers | Regex patterns for each MessageType |
| `ExtractionResult` | Container for extracted messages with metadata |
| `ExtractionConfig` | Configurable extraction settings |
| `ExtractionService` | Pattern-based message classification |
| `ExtractionPipeline` | End-to-end processing with callbacks |

Message types detected:
- **REASONING**: "I'm thinking...", "Let me analyze..."
- **DIALOGUE**: Questions, @mentions, conversations
- **DECISION**: "I've decided...", "Going with..."
- **ACTION**: "Starting...", "Committing...", "Done:"
- **BLOCKER**: "Blocked:", "Waiting on...", "Error:"
- **TECHNICAL**: Code blocks, API explanations

### 3. Permission Service (`roboco/services/permissions.py`)
| Component | Description |
|-----------|-------------|
| `PermissionLevel` | Hierarchy levels (CEO → Board → Main PM → Cell PM → Member) |
| `ChannelPermission` | Defines read/write access per channel |
| `COMMUNICATION_MATRIX` | Who can communicate with whom |
| `NOTIFICATION_TARGETS` | Who can notify whom |
| `TASK_PERMISSIONS` | Task actions by role |
| `PermissionService` | Main service for all permission checks |

Channel permissions implemented:
- Cell channels (backend-cell, frontend-cell, uxui-cell)
- Cross-cell channels (dev-all, qa-all, pm-all, doc-all)
- Management channels (main-pm-board, board-private)
- Special channels (announcements, all-hands)

Auditor has silent read access to all channels.

### 4. API Integration

#### New Dependencies (`roboco/api/deps.py`)
- `PermissionServiceDep` - Injects permission service
- `CurrentAgentContext` - Full agent context from headers
- `require_channel_read()` - Dependency factory for channel read checks
- `require_channel_write()` - Dependency factory for channel write checks
- `require_notification_permission()` - Dependency for notification checks

#### New Routes (`roboco/api/routes/stream.py`)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/stream/chunk` | POST | Process a stream chunk |
| `/api/v1/stream/complete` | POST | Mark stream complete, get content |
| `/api/v1/stream/extract` | POST | Extract messages from content |
| `/api/v1/stream/stats` | GET | Transcription service stats |
| `/api/v1/stream/permissions` | GET | Get agent's permission summary |
| `/api/v1/stream/permissions/channel/{name}` | GET | Check specific channel access |

#### App Integration (`roboco/api/app.py`)
- Services initialized in lifespan context
- `app.state.transcription` - Global transcription service
- `app.state.extraction` - Global extraction pipeline

## File Structure
```
roboco/services/
├── __init__.py           # Service exports
├── transcription.py      # StreamBuffer, TranscriptionService
├── extraction.py         # ExtractionService, ExtractionPipeline
└── permissions.py        # PermissionService, matrices
```

## Next Steps (Phase 3: Intelligence)
Per HOMELAB_TEAM_V0.md section 13.5:
- [ ] Setup Qdrant on NAS
- [ ] Build embedding pipeline
- [ ] Implement Optimal API (RAG queries)
- [ ] Implement Journal API
- [ ] Index existing repositories

## Quick Context Restore
Phase 2 communication services complete. Transcription buffers LLM streams, extraction classifies message types, and permissions enforce the communication matrix from the blueprint. All services are integrated with the FastAPI application and accessible via `/api/v1/stream/*` endpoints. Ready for Phase 3 which adds RAG/knowledge base capabilities.
