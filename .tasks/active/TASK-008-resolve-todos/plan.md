# Implementation Plan: TASK-008 - Resolve All TODOs

## Overview

This plan breaks down the TODO resolution into discrete, testable sub-tasks organized by priority and dependency order.

---

## Phase 1: Critical Path (P0) - ~2.5 hours

### 1.1 Health Check Implementation (30 min)

**File**: `roboco/api/routes/health.py`

**Sub-tasks**:
1. Add database health check function
2. Add Redis health check function
3. Update `readiness_check()` endpoint to call both
4. Handle failures gracefully (return status per service)

**Changes**:
```python
# Add imports
from sqlalchemy import text
from roboco.db import get_async_session
import redis.asyncio as redis
from roboco.config import settings

# Add check functions
async def check_database() -> tuple[str, bool]:
    try:
        async with get_async_session() as session:
            await session.execute(text("SELECT 1"))
        return "ok", True
    except Exception as e:
        return str(e), False

async def check_redis() -> tuple[str, bool]:
    try:
        client = redis.from_url(settings.redis_url)
        await client.ping()
        await client.close()
        return "ok", True
    except Exception as e:
        return str(e), False

# Update endpoint
@router.get("/ready")
async def readiness_check() -> ReadinessResponse:
    db_status, db_ok = await check_database()
    redis_status, redis_ok = await check_redis()
    overall = "ok" if (db_ok and redis_ok) else "degraded"
    return ReadinessResponse(
        status=overall,
        database=db_status,
        redis=redis_status,
    )
```

**Tests**: Call `/ready` with DB up/down, Redis up/down

---

### 1.2 Base Agent LLM Integration (2 hours)

**File**: `roboco/agents/base.py`

**Sub-tasks**:
1. Add Anthropic client initialization
2. Implement `think()` method with actual LLM call
3. Implement `think_and_stream()` with streaming response
4. Implement `send_message()` with HTTP API call
5. Implement `stream_reasoning()` with WebSocket broadcast

**Changes**:

```python
# Add to imports
from anthropic import AsyncAnthropic
import httpx
from roboco.config import settings
from roboco.api.websocket import broadcast_agent_chunk

# Add to Agent.__init__
self._llm_client: AsyncAnthropic | None = None

# Add property
@property
def llm_client(self) -> AsyncAnthropic:
    if self._llm_client is None:
        self._llm_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return self._llm_client

# Implement think()
async def think(self, prompt: str, context: dict[str, Any] | None = None) -> str:
    self.log.debug("Thinking", prompt_length=len(prompt))

    messages = [{"role": "user", "content": prompt}]

    response = await self.llm_client.messages.create(
        model=self.config.model,
        max_tokens=self.config.max_tokens,
        system=self.config.system_prompt,
        messages=messages,
    )

    return response.content[0].text

# Implement think_and_stream()
async def think_and_stream(
    self,
    prompt: str,
    context: dict[str, Any] | None = None,
) -> str:
    self.log.debug("Thinking (streaming)", prompt_length=len(prompt))

    messages = [{"role": "user", "content": prompt}]
    full_response = ""

    async with self.llm_client.messages.stream(
        model=self.config.model,
        max_tokens=self.config.max_tokens,
        system=self.config.system_prompt,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            full_response += text
            await self.stream_reasoning(text)

    return full_response

# Implement send_message()
async def send_message(
    self,
    channel_id: UUID,
    content: str,
    message_type: str = "dialogue",
) -> None:
    self.state.messages_sent += 1
    self.state.last_activity = datetime.now(UTC)

    url = f"http://{settings.host}:{settings.port}/api/v1/messages"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={
            "channel_id": str(channel_id),
            "agent_id": str(self.id),
            "content": content,
            "message_type": message_type,
        })

    self.log.debug("Message sent", channel_id=str(channel_id))

# Implement stream_reasoning()
async def stream_reasoning(self, content: str) -> None:
    await broadcast_agent_chunk(self.id, content)
    self.log.debug("Streamed reasoning", content_length=len(content))
```

**Tests**: Unit test with mocked Anthropic client

---

## Phase 2: Agent Functionality (P1) - ~5.5 hours

### 2.1 Agent API Helper (30 min)

**File**: `roboco/agents/base.py` (add helper method)

```python
async def _api_call(
    self,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Make API call to RoboCo services."""
    url = f"http://{settings.host}:{settings.port}/api/v1{path}"
    async with httpx.AsyncClient() as client:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
```

---

### 2.2 Developer Agent (1.5 hours)

**File**: `roboco/agents/developer.py`

**Sub-tasks**:
1. Implement `_find_paused_task()` - GET `/tasks?status=paused&assigned_to={id}`
2. Implement `_find_assigned_task()` - GET `/tasks?status=pending&assigned_to={id}`
3. Implement `_get_task_title()` - GET `/tasks/{id}`
4. Implement `_read_task_requirements()` - GET `/tasks/{id}` (description + criteria)
5. Implement `_update_task_status()` - PUT `/tasks/{id}`
6. Implement `_check_qa_approved()` - GET `/tasks/{id}` check status
7. Implement `_check_docs_complete()` - GET `/tasks/{id}/handoffs`

**Pattern**:
```python
async def _find_paused_task(self) -> UUID | None:
    result = await self._api_call(
        "GET",
        "/tasks",
        params={"status": "paused", "assigned_to": str(self.id)}
    )
    tasks = result.get("items", [])
    return UUID(tasks[0]["id"]) if tasks else None
```

---

### 2.3 QA Agent (1 hour)

**File**: `roboco/agents/qa.py`

**Sub-tasks**:
1. Implement `_find_awaiting_qa()` - GET `/tasks?status=awaiting_qa&team={team}`
2. Implement `_get_task_title()` - same as developer
3. Implement `_read_task_requirements()` - same as developer
4. Implement `_read_dev_notes()` - GET `/tasks/{id}` (dev_notes field)
5. Implement `_get_task_commits()` - GET `/tasks/{id}` (commits field)
6. Implement `_update_task_status()` - same as developer

---

### 2.4 Documenter Agent (1 hour)

**File**: `roboco/agents/documenter.py`

**Sub-tasks**:
1. Implement all query methods (same pattern as QA)
2. Implement `_phase_publish()` with file writing:

```python
import aiofiles

async def _phase_publish(self, ctx: DocContext) -> None:
    self.log.info("PUBLISH phase", task_id=str(ctx.task_id))

    for doc_spec in ctx.documents_needed:
        if doc_spec.content:
            path = Path(doc_spec.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(path, 'w') as f:
                await f.write(doc_spec.content)
            self.log.info("Published", path=doc_spec.path)

    await self._update_task_status(ctx.task_id, TaskStatus.COMPLETED)
```

---

### 2.5 PM Agents (1 hour)

**File**: `roboco/agents/pm.py`

**Sub-tasks**:
1. Implement task counting methods using `/tasks` with filters
2. Implement agent counting using `/agents` endpoint
3. Implement `_get_pending_questions()` using `/messages?type=dialogue`
4. Implement `_check_task_progress()` using `/tasks/{id}`

---

### 2.6 Board Agents (1 hour)

**File**: `roboco/agents/board.py`

**Sub-tasks**:
1. Implement `_review_feature()` - GET task, check acceptance criteria
2. Implement `_read_channel_silently()` - GET `/channels/{slug}/messages`
3. Implement `_perform_audit()` - Aggregate queries for patterns

---

## Phase 3: Real-Time (P2) - ~2 hours

### 3.1 WebSocket Channel Validation (1 hour)

**File**: `roboco/api/websocket.py`

```python
from roboco.services.permissions import PermissionService

async def validate_channel_access(
    channel_id: UUID,
    agent_id: UUID,
    session: AsyncSession
) -> bool:
    perm_service = PermissionService(session)
    return await perm_service.can_read_channel(agent_id, channel_id)
```

---

### 3.2 Per-Agent Notification Delivery (1 hour)

**File**: `roboco/api/websocket.py`

Add agent-specific connections tracking:

```python
class ConnectionManager:
    def __init__(self) -> None:
        # ... existing ...
        # Add per-agent notification connections
        self.notification_connections: dict[UUID, set[WebSocket]] = {}

    async def connect_notifications(
        self, websocket: WebSocket, agent_id: UUID
    ) -> None:
        await websocket.accept()
        if agent_id not in self.notification_connections:
            self.notification_connections[agent_id] = set()
        self.notification_connections[agent_id].add(websocket)

async def broadcast_notification(
    agent_ids: list[UUID],
    notification_id: UUID,
    notification_type: str,
    subject: str,
    priority: str,
) -> None:
    event = {
        "type": "notification",
        "notification_id": str(notification_id),
        "notification_type": notification_type,
        "subject": subject,
        "priority": priority,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    data = json.dumps(event)

    for agent_id in agent_ids:
        connections = manager.notification_connections.get(agent_id, set())
        await asyncio.gather(
            *[conn.send_text(data) for conn in connections],
            return_exceptions=True,
        )
```

---

## Phase 4: Intelligence (P2) - ~3 hours

### 4.1 LLM-based Message Extraction (1 hour)

**File**: `roboco/services/extraction.py`

```python
async def extract_with_llm(
    self,
    content: str,
    agent_id: UUID,
    channel_id: UUID,
    session_id: UUID,
    group_id: UUID,
    task_id: UUID | None = None,
) -> ExtractionResult:
    from anthropic import AsyncAnthropic
    from roboco.config import settings

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Use LLM to classify message segments
    prompt = f"""Classify the following agent output into message types.
For each distinct segment, identify:
- type: reasoning, dialogue, decision, action, blocker, or technical
- content: the segment text
- confidence: 0.0 to 1.0

Agent output:
{content}

Return as JSON array of objects with type, content, confidence."""

    response = await client.messages.create(
        model="claude-3-haiku-20240307",  # Fast, cheap for classification
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    # Parse response and create ExtractedMessage objects
    # ... parsing logic ...

    return ExtractionResult(...)
```

---

### 4.2 OptimalService RAG Methods (2 hours)

**File**: `roboco/services/optimal.py`

Implement `search()`, `query()`, `index()` using piragi:

```python
async def search(
    self,
    query: str,
    index_types: list[IndexType] | None = None,
    limit: int = 10,
) -> list[SearchResult]:
    """Search across knowledge base indexes."""
    types_to_search = index_types or list(IndexType)
    all_results: list[SearchResult] = []

    for index_type in types_to_search:
        index = self._get_index(index_type)
        # Use piragi's search method
        results = await index.search(query, top_k=limit)
        for r in results:
            all_results.append(SearchResult(
                content=r.content,
                source=r.metadata.get("source", "unknown"),
                score=r.score,
                index_type=index_type,
                metadata=r.metadata,
            ))

    # Sort by score, return top limit
    all_results.sort(key=lambda x: x.score, reverse=True)
    return all_results[:limit]

async def query(
    self,
    query: str,
    context: QueryContext | None = None,
) -> RAGResponse:
    """RAG query with context."""
    # Get relevant context
    results = await self.search(
        query,
        index_types=context.index_types if context else None,
        limit=5,
    )

    # Build context for LLM
    context_text = "\n\n".join([r.content for r in results])

    # Query with RAG context
    prompt = f"""Based on the following context, answer the question.

Context:
{context_text}

Question: {query}

Answer:"""

    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model=settings.default_model,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    return RAGResponse(
        answer=response.content[0].text,
        citations=results,
        query=query,
        context_used=len(results),
    )
```

---

## Validation Checklist

After each phase, verify:

- [ ] `uv run ruff format .` passes
- [ ] `uv run ruff check .` passes
- [ ] `uv run mypy roboco/` passes
- [ ] No TODO comments remain in modified files
- [ ] API calls work against running server

---

## Rollback Plan

Each phase is independent. If issues arise:
1. Revert the specific file changes
2. Leave TODO comments in place for that section
3. Create a new sub-task for the problematic area

---

## Definition of Done

- [ ] All 37 TODOs resolved or converted to tracked issues
- [ ] Health checks verify actual service connectivity
- [ ] Agents can call LLM and receive responses
- [ ] Agents can send messages via API
- [ ] WebSocket notifications delivered to connected agents
- [ ] RAG queries return relevant results
- [ ] All type checks pass
- [ ] All linting passes
