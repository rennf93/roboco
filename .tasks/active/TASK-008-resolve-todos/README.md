# TASK-008: Resolve All TODOs Across Codebase

**Status**: `completed`
**Priority**: P1
**Cell**: Board (cross-cutting)
**Created**: 2025-12-10
**Assigned To**: -

---

## Summary

Resolve all TODO comments scattered across agent implementations, API routes, and services. The codebase has a complete service layer (TaskService, NotificationService, JournalService, etc.) and MCP servers that wrap them, but the agent implementations contain placeholder methods with TODO comments that need to be wired up to these existing services.

## Context

### What Exists (Already Implemented)

| Component | Location | Status |
|-----------|----------|--------|
| TaskService | `roboco/services/task.py` | Complete (717 lines) |
| NotificationService | `roboco/services/notification.py` | Complete (189 lines) |
| JournalService | `roboco/services/journal.py` | Complete (691 lines) |
| KanbanService | `roboco/services/kanban.py` | Complete (489 lines) |
| MetricsService | `roboco/services/metrics.py` | Complete (592 lines) |
| ExtractionService | `roboco/services/extraction.py` | Complete (462 lines) |
| OptimalService | `roboco/services/optimal.py` | Partial (core methods stubbed) |
| Task MCP Server | `roboco/mcp/task_server.py` | Complete (1173 lines) |
| Message MCP Server | `roboco/mcp/message_server.py` | Complete (484 lines) |
| Notify MCP Server | `roboco/mcp/notify_server.py` | Complete (486 lines) |
| Journal MCP Server | `roboco/mcp/journal_server.py` | Complete (600 lines) |

### What's Missing (TODOs to Resolve)

37 TODO items across 9 files, categorized into 4 work packages.

---

## Acceptance Criteria

- [ ] All agent methods call appropriate services instead of returning placeholders
- [ ] Health check endpoints verify actual database and Redis connectivity
- [ ] WebSocket broadcasts are wired to actual notification delivery
- [ ] LLM integration methods use Anthropic/OpenAI clients
- [ ] OptimalService core methods are implemented with piragi
- [ ] All TODO comments are removed or converted to tracked issues
- [ ] Tests pass (if any exist for modified code)
- [ ] Type checking passes (`mypy src/`)

---

## Work Packages

### WP-1: Agent Service Integration (24 TODOs) - P1

Wire agent methods to existing services. The agents need database sessions injected or access to API endpoints.

**Strategy**: Agents will call the Task/Message/Notification APIs via HTTP (same pattern as MCP servers) rather than direct service injection.

| File | Method | TODO | Resolution |
|------|--------|------|------------|
| `agents/base.py:322` | `send_message` | Integrate with Messaging API | Call `/api/v1/messages` endpoint |
| `agents/base.py:339` | `stream_reasoning` | Integrate with WebSocket streaming | Call `broadcast_agent_chunk()` |
| `agents/base.py:357` | `think` | Integrate with LLM provider | Use `anthropic` or `openai` client |
| `agents/base.py:378` | `think_and_stream` | Integrate with LLM + streaming | Stream via client + broadcast |
| `agents/board.py:151` | `_review_feature` | Check acceptance criteria | Query task, validate criteria |
| `agents/board.py:578` | `_read_channel_silently` | Query messaging API silent | Call `/api/v1/channels/{id}/messages` |
| `agents/board.py:591` | `_perform_audit` | Implement audits | Query tasks/messages for patterns |
| `agents/developer.py:497` | `_find_paused_task` | Query task API | Call `/api/v1/tasks?status=paused&assigned_to={id}` |
| `agents/developer.py:502` | `_find_assigned_task` | Query task API | Call `/api/v1/tasks?assigned_to={id}` |
| `agents/developer.py:515` | `_get_task_title` | Query task API | Call `/api/v1/tasks/{id}` |
| `agents/developer.py:520` | `_read_task_requirements` | Read from .tasks/ | Read file or call API |
| `agents/developer.py:525` | `_update_task_status` | Update via API | Call `PUT /api/v1/tasks/{id}` |
| `agents/developer.py:530` | `_check_qa_approved` | Check via API | Query task status |
| `agents/developer.py:535` | `_check_docs_complete` | Check via API | Query handoff status |
| `agents/documenter.py:408` | `_phase_publish` | Write file | Use `aiofiles` to write docs |
| `agents/documenter.py:428-463` | 7 methods | Various queries | Call appropriate API endpoints |
| `agents/pm.py:321-398` | 9 methods | Task/Agent queries | Call task/agent APIs |
| `agents/qa.py:432-457` | 6 methods | Task queries | Call task API |

**Dependencies**: None - services exist
**Effort**: Medium (mostly HTTP client calls)

---

### WP-2: Health Check Implementation (2 TODOs) - P0

Make health checks actually verify connectivity.

| File | Line | TODO | Resolution |
|------|------|------|------------|
| `api/routes/health.py:58` | readiness | Check DB connection | Use `session.execute(text("SELECT 1"))` |
| `api/routes/health.py:60` | readiness | Check Redis | Use `redis.ping()` |

**Dependencies**: Database and Redis clients
**Effort**: Small

---

### WP-3: WebSocket Notification Delivery (2 TODOs) - P2

Complete WebSocket integration for real-time notifications.

| File | Line | TODO | Resolution |
|------|------|------|------------|
| `api/websocket.py:236` | channel_stream | Validate agent access | Call PermissionService |
| `api/websocket.py:432` | broadcast_notification | Per-agent delivery | Track agent connections, route |

**Dependencies**: ConnectionManager enhancements
**Effort**: Medium

---

### WP-4: OptimalService / LLM Integration (3 TODOs) - P2

Complete RAG and LLM integration.

| File | Line | TODO | Resolution |
|------|------|------|------------|
| `services/extraction.py:379` | extract_with_llm | LLM classification | Use Anthropic client for classification |
| `services/optimal.py` | search/query/index | RAG operations | Implement with piragi methods |

**Dependencies**: piragi library, Anthropic/OpenAI clients
**Effort**: Large

---

## Implementation Order

```
Phase 1 (Critical Path):
├── WP-2: Health Checks (30 min) - Immediate value
├── WP-1a: Base Agent LLM Integration (2 hours) - Enables all agents
│   └── think(), think_and_stream(), send_message()
│
Phase 2 (Agent Functionality):
├── WP-1b: Developer Agent (1.5 hours)
├── WP-1c: QA Agent (1 hour)
├── WP-1d: Documenter Agent (1 hour)
├── WP-1e: PM Agents (1 hour)
├── WP-1f: Board Agents (1 hour)
│
Phase 3 (Real-Time):
├── WP-3: WebSocket (2 hours)
│
Phase 4 (Intelligence):
└── WP-4: OptimalService (3 hours)

Total Estimated: ~13-15 hours
```

---

## Technical Notes

### Agent API Access Pattern

Agents should use an HTTP client to call the RoboCo API (same as MCP servers):

```python
import httpx
from roboco.config import settings

async def _call_api(self, method: str, path: str, **kwargs) -> dict:
    """Make API call to RoboCo services."""
    url = f"http://{settings.host}:{settings.port}/api/v1{path}"
    async with httpx.AsyncClient() as client:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
```

### LLM Client Integration

Use the Anthropic client from settings:

```python
from anthropic import AsyncAnthropic
from roboco.config import settings

client = AsyncAnthropic(api_key=settings.anthropic_api_key)

async def think(self, prompt: str, context: dict | None = None) -> str:
    messages = [{"role": "user", "content": prompt}]
    response = await client.messages.create(
        model=settings.default_model,
        max_tokens=self.config.max_tokens,
        system=self.config.system_prompt,
        messages=messages,
    )
    return response.content[0].text
```

### Database Health Check

```python
from sqlalchemy import text
from roboco.db import get_async_session

async def check_database() -> bool:
    try:
        async with get_async_session() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
```

---

## Files to Modify

| Priority | File | Changes |
|----------|------|---------|
| P0 | `roboco/api/routes/health.py` | Add actual health checks |
| P1 | `roboco/agents/base.py` | Wire LLM + messaging |
| P1 | `roboco/agents/developer.py` | Wire task API calls |
| P1 | `roboco/agents/qa.py` | Wire task API calls |
| P1 | `roboco/agents/documenter.py` | Wire task API + file writes |
| P1 | `roboco/agents/pm.py` | Wire task/agent API calls |
| P1 | `roboco/agents/board.py` | Wire task/message API calls |
| P2 | `roboco/api/websocket.py` | Complete notification delivery |
| P2 | `roboco/services/extraction.py` | Add LLM classification |
| P2 | `roboco/services/optimal.py` | Implement RAG methods |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Anthropic API key not configured | Agents can't think | Add fallback to local Ollama |
| Database not running | Health checks fail | Document startup requirements |
| Circular imports | Import errors | Use lazy imports or dependency injection |
| Rate limiting on LLM | Agents blocked | Implement retry with backoff |

---

## Blockers

None identified - all dependencies exist.

---

## Journal

| Date | Author | Entry |
|------|--------|-------|
| 2025-12-10 | Claude | Created task. Analyzed 37 TODOs across 9 files. Categorized into 4 work packages. |
| 2025-12-10 | Claude | Completed all phases. Implemented health checks, LLM integration in base agent, wired all agent types to APIs, added WebSocket channel validation and per-agent notification delivery, implemented LLM extraction in ExtractionService. |

