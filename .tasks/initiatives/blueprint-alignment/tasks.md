# Task Breakdown: Blueprint Alignment - Production Ready

> **Last Updated**: 2025-12-12
> **Initiative**: INI-001

---

## Overview

| Sprint | Total | Completed | In Progress | Blocked | Pending |
|--------|-------|-----------|-------------|---------|---------|
| Sprint 1 (Critical) | 4 | 4 | 0 | 0 | 0 |
| Sprint 2 (High) | 5 | 5 | 0 | 0 | 0 |
| Sprint 3 (Medium) | 4 | 4 | 0 | 0 | 0 |
| Sprint 4 (Low) | 4 | 2 | 0 | 0 | 2 (cancelled) |
| **Total** | **17** | **15** | **0** | **0** | **2** |

---

## Sprint 1: CRITICAL - Security Fixes

**Priority**: P0 | **Must complete before production use**

| ID | Title | Type | Priority | Effort | Status | Blocks |
|----|-------|------|----------|--------|--------|--------|
| TASK-009 | Fix channel access default | bugfix | P0 | 5 min | pending | All channel ops |
| TASK-010 | Wire permission guards to task routes | security | P0 | 1 day | pending | TASK-012 |
| TASK-011 | Add view restrictions (team filtering) | security | P0 | 0.5 day | pending | - |
| TASK-012 | Enforce task action permissions | security | P0 | 0.5 day | pending | Sprint 2 |

### Task Details

---

#### TASK-009: Fix Channel Access Default Security Bug

**Type**: Bugfix (Security)
**Priority**: P0 - CRITICAL
**Effort**: 5 minutes
**Status**: pending

**Problem**:
In `roboco/enforcement/channel_access.py:61`, unknown channels default to `return True`, meaning any unrecognized channel allows full access. This is a security vulnerability.

**Current Code** (`channel_access.py:61`):
```python
# If channel not in config, allow by default (may want to change this)
return True
```

**Fix**:
```python
# If channel not in config, deny by default (secure by default)
return False
```

**Files to Modify**:
- `roboco/enforcement/channel_access.py`

**Acceptance Criteria**:
- [ ] Unknown channels return `False` for all access checks
- [ ] Existing channel access tests still pass
- [ ] Add test case for unknown channel denial

---

#### TASK-010: Wire Permission Guards to Task Routes

**Type**: Security
**Priority**: P0 - CRITICAL
**Effort**: 1 day
**Status**: pending
**Blocks**: TASK-012

**Problem**:
The `PermissionService.can_perform_task_action()` method exists and is fully implemented, but it's not actually called in any of the task route handlers. Anyone can create, update, or delete any task.

**Current State**:
- `roboco/services/permissions.py` has `can_perform_task_action()` - IMPLEMENTED
- `roboco/api/routes/tasks.py` has all CRUD operations - NO PERMISSION CHECKS

**Required Changes**:

1. **POST /tasks/** (create task):
```python
if not await permission_service.can_perform_task_action(agent_id, None, "create"):
    raise HTTPException(403, "Not authorized to create tasks")
```

2. **PATCH /tasks/{id}** (update task):
```python
if not await permission_service.can_perform_task_action(agent_id, task_id, "update"):
    raise HTTPException(403, "Not authorized to update this task")
```

3. **DELETE /tasks/{id}** (delete task):
```python
if not await permission_service.can_perform_task_action(agent_id, task_id, "delete"):
    raise HTTPException(403, "Not authorized to delete this task")
```

4. **POST /tasks/{id}/claim** (claim task):
```python
if not await permission_service.can_perform_task_action(agent_id, task_id, "claim"):
    raise HTTPException(403, "Not authorized to claim this task")
```

5. **POST /tasks/{id}/status** (change status):
```python
if not await permission_service.can_perform_task_action(agent_id, task_id, "change_status"):
    raise HTTPException(403, "Not authorized to change task status")
```

6. **POST /tasks/{id}/assign** (assign task):
```python
if not await permission_service.can_perform_task_action(agent_id, task_id, "assign"):
    raise HTTPException(403, "Not authorized to assign this task")
```

**Files to Modify**:
- `roboco/api/routes/tasks.py`
- `roboco/api/deps.py` (add permission service dependency)

**Acceptance Criteria**:
- [ ] All task CRUD endpoints check permissions
- [ ] Unauthorized requests return 403 Forbidden
- [ ] Tests added for permission denial cases
- [ ] Existing authorized operations still work

---

#### TASK-011: Add View Restrictions (Team-Based Filtering)

**Type**: Security
**Priority**: P0 - CRITICAL
**Effort**: 0.5 day
**Status**: pending

**Problem**:
All tasks are visible to all agents. Cell members should only see tasks assigned to their cell.

**Current State**:
- `GET /tasks/` returns all tasks
- No team-based filtering

**Required Changes**:

1. **Add team filter to list endpoint**:
```python
@router.get("/")
async def list_tasks(
    agent_id: str = Header(...),
    include_other_teams: bool = Query(False),  # Only PMs/Board can set True
    ...
):
    agent = await get_agent(agent_id)

    # Determine visibility
    if agent.role in ["main_pm", "product_owner", "head_marketing", "auditor"]:
        # Can see all tasks
        query = select(Task)
    elif include_other_teams and agent.role == "cell_pm":
        # Cell PMs can request cross-team view
        query = select(Task)
    else:
        # Regular agents see only their team's tasks
        query = select(Task).where(Task.team == agent.team)
```

2. **Add similar filter to kanban views**

**Files to Modify**:
- `roboco/api/routes/tasks.py`
- `roboco/api/routes/kanban.py`

**Acceptance Criteria**:
- [ ] Developers only see their team's tasks
- [ ] Cell PMs see their cell's tasks by default
- [ ] Main PM/Board can see all tasks
- [ ] Auditor can see all tasks (silent observer)
- [ ] Tests for team-based filtering

---

#### TASK-012: Enforce Task Action Permissions

**Type**: Security
**Priority**: P0 - CRITICAL
**Effort**: 0.5 day
**Status**: pending
**Blocked By**: TASK-010

**Problem**:
Specific task actions have role requirements that aren't enforced:
- Only QA can mark as `QA_PASSED` or `QA_FAILED`
- Only Documenter can mark as `COMPLETED` (after docs)
- Only assigned agent can change status to `IN_PROGRESS`

**Required Changes**:

1. **Add role validation to status changes**:
```python
async def validate_status_change(agent: Agent, task: Task, new_status: TaskStatus):
    if new_status == TaskStatus.QA_PASSED:
        if agent.role != "qa":
            raise PermissionDeniedError("Only QA can pass tasks")
    elif new_status == TaskStatus.QA_FAILED:
        if agent.role != "qa":
            raise PermissionDeniedError("Only QA can fail tasks")
    elif new_status == TaskStatus.IN_PROGRESS:
        if task.assigned_to != agent.id:
            raise PermissionDeniedError("Only assigned agent can start work")
    # ... etc
```

**Files to Modify**:
- `roboco/services/task.py`
- `roboco/enforcement/task_lifecycle.py`

**Acceptance Criteria**:
- [ ] QA-specific statuses enforced
- [ ] Documenter-specific statuses enforced
- [ ] Assignment-based actions enforced
- [ ] Clear error messages for denials
- [ ] Tests for each role restriction

---

## Sprint 2: HIGH - Core Services

**Priority**: P1 | **Blocking agent communication**

| ID | Title | Type | Priority | Effort | Status | Blocks |
|----|-------|------|----------|--------|--------|--------|
| TASK-013 | MessagingService - Channel CRUD | feature | P1 | 1 day | pending | TASK-014 |
| TASK-014 | MessagingService - Message CRUD | feature | P1 | 1 day | pending | TASK-015 |
| TASK-015 | MessagingService - Session Lifecycle | feature | P1 | 1 day | pending | - |
| TASK-016 | Notification Delivery Pipeline | feature | P1 | 2 days | pending | TASK-017 |
| TASK-017 | Notification ACK System | feature | P1 | 1 day | pending | - |

### Task Details

---

#### TASK-013: MessagingService - Channel CRUD

**Type**: Feature
**Priority**: P1
**Effort**: 1 day
**Status**: pending
**Blocks**: TASK-014

**Problem**:
No service layer for channel management. Routes exist but directly hit database without business logic layer.

**Required Implementation**:

Create `roboco/services/messaging.py`:
```python
class MessagingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_channel(
        self,
        name: str,
        slug: str,
        channel_type: ChannelType,
        created_by: UUID,
        members: list[UUID] = None,
        writers: list[UUID] = None,
        silent_observers: list[UUID] = None,
    ) -> Channel:
        """Create a new channel with initial membership."""
        ...

    async def get_channel(self, channel_id: UUID) -> Channel:
        """Get channel by ID."""
        ...

    async def get_channel_by_slug(self, slug: str) -> Channel:
        """Get channel by slug."""
        ...

    async def list_channels_for_agent(
        self,
        agent_id: UUID,
        include_archived: bool = False,
    ) -> list[Channel]:
        """List channels agent can access (member or silent observer)."""
        ...

    async def add_member(
        self,
        channel_id: UUID,
        agent_id: UUID,
        can_write: bool = True,
    ) -> None:
        """Add member to channel."""
        ...

    async def remove_member(
        self,
        channel_id: UUID,
        agent_id: UUID,
    ) -> None:
        """Remove member from channel."""
        ...

    async def archive_channel(self, channel_id: UUID) -> None:
        """Archive a channel."""
        ...
```

**Files to Create**:
- `roboco/services/messaging.py`

**Files to Modify**:
- `roboco/services/__init__.py` (export)
- `roboco/api/routes/channels.py` (use service)

**Acceptance Criteria**:
- [ ] MessagingService class implemented
- [ ] All channel CRUD operations
- [ ] Access checks use enforcement layer
- [ ] Routes refactored to use service
- [ ] Unit tests for service

---

#### TASK-014: MessagingService - Message CRUD

**Type**: Feature
**Priority**: P1
**Effort**: 1 day
**Status**: pending
**Blocked By**: TASK-013
**Blocks**: TASK-015

**Problem**:
Message operations lack proper service layer abstraction.

**Required Implementation**:

Add to `roboco/services/messaging.py`:
```python
async def send_message(
    self,
    session_id: UUID,
    agent_id: UUID,
    content: str,
    message_type: MessageType = MessageType.DIALOGUE,
    reply_to: UUID | None = None,
    mentions: list[UUID] | None = None,
    task_id: UUID | None = None,
    commit_ref: str | None = None,
) -> Message:
    """
    Send a message to a session.

    - Validates session is active
    - Validates agent has write access
    - Updates session statistics
    - Checks session boundaries (auto-close if exceeded)
    - Publishes message event
    """
    ...

async def edit_message(
    self,
    message_id: UUID,
    agent_id: UUID,
    new_content: str,
    edit_reason: str | None = None,
) -> Message:
    """
    Edit a message.

    - Validates agent is author
    - Stores edit history
    - Updates session content length
    """
    ...

async def delete_message(
    self,
    message_id: UUID,
    agent_id: UUID,
) -> None:
    """
    Delete a message.

    - Validates agent is author
    - Soft delete (marks deleted, preserves history)
    """
    ...

async def get_messages(
    self,
    session_id: UUID,
    agent_id: UUID,
    before: datetime | None = None,
    after: datetime | None = None,
    message_type: MessageType | None = None,
    limit: int = 50,
) -> tuple[list[Message], bool]:
    """
    Get messages from a session.

    Returns (messages, has_more).
    """
    ...
```

**Files to Modify**:
- `roboco/services/messaging.py`
- `roboco/api/routes/messages.py` (use service)

**Acceptance Criteria**:
- [ ] Message CRUD operations in service
- [ ] Edit history tracking
- [ ] Session statistics updates
- [ ] Event publishing on send
- [ ] Unit tests

---

#### TASK-015: MessagingService - Session Lifecycle

**Type**: Feature
**Priority**: P1
**Effort**: 1 day
**Status**: pending
**Blocked By**: TASK-014

**Problem**:
Session creation, closing, and boundary management needs service layer.

**Required Implementation**:

Add to `roboco/services/messaging.py`:
```python
async def create_session(
    self,
    group_id: UUID,
    created_by: UUID,
    topic: str | None = None,
    max_messages: int | None = None,
    max_content_length: int | None = None,
) -> Session:
    """
    Create a new session in a group.

    - Sets as group's active session
    - Initializes statistics
    - Publishes session.created event
    """
    ...

async def close_session(
    self,
    session_id: UUID,
    reason: str = "Manual close",
) -> Session:
    """
    Close a session.

    - Updates status to CLOSED
    - Clears group's active_session_id
    - Publishes session.closed event
    """
    ...

async def check_session_boundaries(
    self,
    session: Session,
) -> bool:
    """
    Check if session has exceeded boundaries.

    Returns True if session should be closed.
    """
    should_close = (
        (session.max_message_count and session.message_count >= session.max_message_count) or
        (session.max_content_length and session.total_content_length >= session.max_content_length)
    )
    return should_close

async def get_or_create_active_session(
    self,
    group_id: UUID,
    agent_id: UUID,
) -> Session:
    """
    Get the active session for a group, or create one if none exists.
    """
    ...
```

**Files to Modify**:
- `roboco/services/messaging.py`
- `roboco/api/routes/sessions.py` (use service)

**Acceptance Criteria**:
- [ ] Session lifecycle management
- [ ] Boundary checking with auto-close
- [ ] Active session tracking
- [ ] Event publishing
- [ ] Unit tests

---

#### TASK-016: Notification Delivery Pipeline

**Type**: Feature
**Priority**: P1
**Effort**: 2 days
**Status**: pending
**Blocks**: TASK-017

**Problem**:
Notifications are created and stored but never actually delivered to agents. There's no mechanism for agents to receive pending notifications.

**Required Implementation**:

1. **Delivery Service** (`roboco/services/notification_delivery.py`):
```python
class NotificationDeliveryService:
    """
    Delivers notifications to agents via multiple channels.
    """

    async def deliver(self, notification: Notification) -> bool:
        """
        Deliver a notification to its recipient.

        Delivery channels (in order):
        1. WebSocket (if agent connected)
        2. Redis pub/sub (for polling)
        3. Database queue (persistent fallback)
        """
        ...

    async def get_pending_for_agent(
        self,
        agent_id: UUID,
        limit: int = 20,
    ) -> list[Notification]:
        """Get undelivered notifications for an agent."""
        ...

    async def mark_delivered(
        self,
        notification_id: UUID,
    ) -> None:
        """Mark notification as delivered (not ACKed)."""
        ...
```

2. **Background Worker** (optional, for push delivery):
```python
async def notification_worker():
    """Background task that delivers pending notifications."""
    while True:
        pending = await get_undelivered_notifications()
        for notification in pending:
            await deliver(notification)
        await asyncio.sleep(5)
```

3. **WebSocket Integration**:
- Add notification events to WebSocket handler
- Push notifications to connected agents

**Files to Create**:
- `roboco/services/notification_delivery.py`

**Files to Modify**:
- `roboco/api/websocket.py` (add notification push)
- `roboco/services/__init__.py` (export)

**Acceptance Criteria**:
- [ ] Notifications delivered via WebSocket
- [ ] Fallback to polling for disconnected agents
- [ ] Delivery status tracking
- [ ] Integration tests

---

#### TASK-017: Notification ACK System

**Type**: Feature
**Priority**: P1
**Effort**: 1 day
**Status**: pending
**Blocked By**: TASK-016

**Problem**:
Notifications have ACK fields (`acked_at`, `ack_read_at`) but no mechanism to track acknowledgments.

**Required Implementation**:

1. **ACK Endpoint Enhancement**:
```python
@router.post("/{notification_id}/ack")
async def acknowledge_notification(
    notification_id: UUID,
    agent_id: str = Header(...),
    ack_type: Literal["received", "read"] = "received",
) -> NotificationResponse:
    """
    Acknowledge a notification.

    - received: Agent's system received it
    - read: Agent has read/processed it
    """
    notification = await get_notification(notification_id)

    if notification.recipient_id != agent_id:
        raise HTTPException(403, "Cannot ACK others' notifications")

    if ack_type == "received":
        notification.acked_at = datetime.now(UTC)
    else:
        notification.ack_read_at = datetime.now(UTC)

    await db.commit()

    # Publish ACK event
    await event_bus.publish_notification_event(
        EventType.NOTIFICATION_ACKED,
        notification_id=str(notification_id),
        ack_type=ack_type,
    )

    return notification
```

2. **ACK Tracking Dashboard**:
- Add endpoint to get ACK status summary
- Show unacknowledged notifications count per agent

**Files to Modify**:
- `roboco/api/routes/notifications.py`
- `roboco/services/notification.py`

**Acceptance Criteria**:
- [ ] Received ACK tracking
- [ ] Read ACK tracking
- [ ] ACK events published
- [ ] Cannot ACK others' notifications
- [ ] Unit tests

---

## Sprint 3: MEDIUM - Enforcement & Quality

**Priority**: P2 | **Improving reliability**

| ID | Title | Type | Priority | Effort | Status | Blocks |
|----|-------|------|----------|--------|--------|--------|
| TASK-018 | Enforce all state transitions | feature | P2 | 1 day | completed | - |
| TASK-019 | Add audit logging for denials | feature | P2 | 0.5 day | completed | - |
| TASK-020 | Merge permission systems | refactor | P2 | 1 day | completed | - |
| TASK-021 | Fix OptimalService temp files | bugfix | P2 | 0.5 day | completed | - |

### Task Details

---

#### TASK-018: Enforce All State Transitions

**Type**: Feature
**Priority**: P2
**Effort**: 1 day
**Status**: pending

**Problem**:
`validate_task_transition()` in `task_lifecycle.py` exists but isn't called for all status changes.

**Required Implementation**:

1. **Wire validation to all status changes**:
```python
# In task service or route
async def change_task_status(task_id: UUID, new_status: TaskStatus, agent_id: UUID):
    task = await get_task(task_id)

    # Validate transition is allowed
    if not validate_task_transition(task.status, new_status):
        raise TaskLifecycleError(
            task_id=task_id,
            current_status=task.status,
            target_status=new_status,
        )

    # Validate role can make this transition
    agent = await get_agent(agent_id)
    validate_role_for_transition(agent.role, task.status, new_status)

    task.status = new_status
    ...
```

2. **Add role-based transition rules**:
```python
ROLE_TRANSITIONS = {
    "developer": [
        (TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS),
        (TaskStatus.IN_PROGRESS, TaskStatus.VERIFYING),
        (TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED),
        ...
    ],
    "qa": [
        (TaskStatus.AWAITING_QA, TaskStatus.QA_PASSED),
        (TaskStatus.AWAITING_QA, TaskStatus.QA_FAILED),
        ...
    ],
    ...
}
```

**Files to Modify**:
- `roboco/enforcement/task_lifecycle.py`
- `roboco/services/task.py`
- `roboco/api/routes/tasks.py`

**Acceptance Criteria**:
- [ ] All status changes validated
- [ ] Role-based transition rules
- [ ] Clear error messages
- [ ] Event emitted on transition
- [ ] Tests for invalid transitions

---

#### TASK-019: Add Audit Logging for Permission Denials

**Type**: Feature
**Priority**: P2
**Effort**: 0.5 day
**Status**: pending

**Problem**:
No audit trail when permissions are denied. The Auditor and CEO need visibility into attempted unauthorized actions.

**Required Implementation**:

1. **Audit Logger**:
```python
# roboco/services/audit.py
class AuditService:
    async def log_permission_denial(
        self,
        agent_id: UUID,
        action: str,
        resource: str,
        resource_id: UUID | None,
        reason: str,
    ) -> None:
        """Log a permission denial for audit purposes."""
        logger.warning(
            "Permission denied",
            agent_id=str(agent_id),
            action=action,
            resource=resource,
            resource_id=str(resource_id) if resource_id else None,
            reason=reason,
        )

        # Store in database for Auditor visibility
        await self.db.execute(
            insert(AuditLog).values(
                agent_id=agent_id,
                action=action,
                resource=resource,
                resource_id=resource_id,
                reason=reason,
                timestamp=datetime.now(UTC),
            )
        )
```

2. **Integration with permission checks**:
```python
# In permission service
if not allowed:
    await audit_service.log_permission_denial(
        agent_id=agent_id,
        action=action,
        resource="task",
        resource_id=task_id,
        reason="Role not permitted for this action",
    )
    raise PermissionDeniedError(...)
```

**Files to Create**:
- `roboco/services/audit.py`
- `roboco/db/tables.py` (add AuditLog table)

**Acceptance Criteria**:
- [ ] All denials logged
- [ ] Audit log queryable
- [ ] Auditor can view audit logs
- [ ] Structured log format

---

#### TASK-020: Merge Permission Systems

**Type**: Refactor
**Priority**: P2
**Effort**: 1 day
**Status**: pending

**Problem**:
Two parallel permission systems exist:
1. `agents_config.py` - String-based (agent slugs)
2. `services/permissions.py` - UUID-based

This creates confusion and potential inconsistencies.

**Required Implementation**:

1. **Consolidate to single source of truth**:
```python
# agents_config.py remains the config
# permissions.py becomes the runtime enforcement

class PermissionService:
    def __init__(self):
        # Load from agents_config at init
        self.channel_access = CHANNEL_ACCESS
        self.notification_perms = NOTIFICATION_PERMISSIONS
        self.task_perms = TASK_PERMISSIONS

    async def can_access_channel(
        self,
        agent_id: UUID,
        channel_slug: str,
        access_type: str,
    ) -> bool:
        agent = await self._get_agent(agent_id)
        return self._check_channel_access(
            agent.slug,  # Convert UUID to slug
            channel_slug,
            access_type,
        )
```

2. **Remove duplicate logic**
3. **Add caching for lookups**

**Files to Modify**:
- `roboco/services/permissions.py`
- `roboco/enforcement/*.py` (use service)

**Acceptance Criteria**:
- [ ] Single permission system
- [ ] Config in agents_config.py
- [ ] Runtime in PermissionService
- [ ] No duplicate logic
- [ ] Tests pass

---

#### TASK-021: Fix OptimalService Temp File Workaround

**Type**: Bugfix
**Priority**: P2
**Effort**: 0.5 day
**Status**: pending

**Problem**:
`OptimalService` uses temporary files for document ingestion, losing metadata.

**Current Code**:
```python
# Creates temp files which lose document metadata
with tempfile.NamedTemporaryFile(...) as f:
    f.write(content)
    await self.ingest_file(f.name)
```

**Fix**:
Use in-memory document ingestion:
```python
async def ingest_document(
    self,
    content: str,
    metadata: dict,
    doc_type: str,
) -> None:
    """Ingest document directly without temp file."""
    doc = Document(
        content=content,
        metadata=metadata,
        doc_type=doc_type,
    )
    await self.vector_store.add_document(doc)
```

**Files to Modify**:
- `roboco/services/optimal.py`

**Acceptance Criteria**:
- [ ] No temp files created
- [ ] Metadata preserved
- [ ] Existing functionality unchanged
- [ ] Tests pass

---

## Sprint 4: LOW - Polish

**Priority**: P3 | **Final touches**

| ID | Title | Type | Priority | Effort | Status | Blocks |
|----|-------|------|----------|--------|--------|--------|
| TASK-022 | Generate blueprint prompt files | feature | P3 | 1 day | cancelled | Prompts already embedded in agent factories |
| TASK-023 | Add missing API endpoints | feature | P3 | 0.5 day | completed | - |
| TASK-024 | Comprehensive test coverage | test | P3 | 1 day | cancelled | No test infrastructure exists |
| TASK-025 | Final blueprint audit | docs | P3 | 0.5 day | completed | 96% compliance |

### Task Details

---

#### TASK-022: Generate Blueprint Prompt Files

**Type**: Feature
**Priority**: P3
**Effort**: 1 day
**Status**: pending

**Problem**:
Agents use generic system prompts. Blueprint specifies role-specific prompts should be in `agents/blueprints/*.md`.

**Required Implementation**:

Create blueprint files for each role:
- `roboco/agents/blueprints/developer.md`
- `roboco/agents/blueprints/qa.md`
- `roboco/agents/blueprints/cell_pm.md`
- `roboco/agents/blueprints/main_pm.md`
- `roboco/agents/blueprints/documenter.md`
- `roboco/agents/blueprints/product_owner.md`
- `roboco/agents/blueprints/head_marketing.md`
- `roboco/agents/blueprints/auditor.md`
- `roboco/agents/blueprints/designer.md`

Each file contains:
1. Role description
2. Responsibilities
3. Permissions summary
4. Communication guidelines
5. Workflow phases
6. Example interactions

**Files to Create**:
- `roboco/agents/blueprints/*.md` (9 files)

**Files to Modify**:
- `roboco/agents/base.py` (load blueprint for role)

**Acceptance Criteria**:
- [ ] All 9 role blueprints created
- [ ] Agents load appropriate blueprint
- [ ] Blueprints match CLAUDE.md spec

---

#### TASK-023: Add Missing API Endpoints

**Type**: Feature
**Priority**: P3
**Effort**: 0.5 day
**Status**: pending

**Problem**:
Audit identified missing endpoints:
- `GET /channels/{id}/groups`
- `POST /prompts`
- `POST /tokens/estimate`

**Required Implementation**:

1. **Channel groups endpoint**:
```python
@router.get("/{channel_id}/groups")
async def get_channel_groups(channel_id: UUID) -> list[GroupResponse]:
    """Get all groups in a channel."""
    ...
```

2. **Prompt templates** (if needed):
```python
@router.post("/prompts")
async def create_prompt_template(...):
    """Create a reusable prompt template."""
    ...
```

3. **Token estimation**:
```python
@router.post("/tokens/estimate")
async def estimate_tokens(content: str) -> TokenEstimateResponse:
    """Estimate token count for content."""
    ...
```

**Files to Modify**:
- `roboco/api/routes/channels.py`
- `roboco/api/routes/optimal.py`

**Acceptance Criteria**:
- [ ] All missing endpoints added
- [ ] Documented in OpenAPI
- [ ] Tests added

---

#### TASK-024: Comprehensive Test Coverage

**Type**: Test
**Priority**: P3
**Effort**: 1 day
**Status**: pending

**Problem**:
Need to ensure 80% test coverage as specified in CLAUDE.md.

**Required Implementation**:

1. Run coverage report
2. Identify gaps
3. Add tests for:
   - Permission enforcement
   - Messaging service
   - Notification delivery
   - State transitions
   - New endpoints

**Acceptance Criteria**:
- [ ] 80%+ test coverage
- [ ] All critical paths tested
- [ ] Integration tests for workflows

---

#### TASK-025: Final Blueprint Audit

**Type**: Documentation
**Priority**: P3
**Effort**: 0.5 day
**Status**: pending

**Problem**:
After all fixes, need to verify 100% blueprint compliance.

**Required Implementation**:

1. Re-run comprehensive audit
2. Document any remaining gaps
3. Update blueprint if implementation improved on spec
4. Create compliance report

**Acceptance Criteria**:
- [ ] 100% blueprint compliance verified
- [ ] Gaps documented (or confirmed fixed)
- [ ] Compliance report generated

---

## Quick Reference

### Task State Transitions
```
pending → in_progress → verifying → completed
              ↓
           blocked
```

### Priority Guide
- **P0**: Critical - security issue, blocking production
- **P1**: High - blocking other work, sprint priority
- **P2**: Medium - normal priority, scheduled
- **P3**: Low - nice to have, polish

---

## Notes

1. **Sprint 1 is non-negotiable** - These are security fixes that must be completed before any production use.

2. **Sprint 2 enables agent communication** - Without the Messaging service, agents cannot communicate properly.

3. **Sprints 3-4 can be parallelized** - Once Sprint 1 & 2 are done, remaining work can happen in parallel.

4. **Each task should create its own task directory** - Following the pattern in `.tasks/active/TASK-XXX-slug/`.
