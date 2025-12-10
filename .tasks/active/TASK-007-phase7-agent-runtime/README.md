# TASK-007: Phase 7 - Agent Runtime & Enforcement Architecture

## Status
- **State**: verifying
- **Priority**: P0
- **Cell**: board
- **Complexity**: HIGH
- **Progress**: 100% (Implementation complete, ready for verification)

## Dates
- **Created**: 2025-12-10
- **Started**: 2025-12-10
- **Implemented**: 2025-12-10
- **Target**: TBD

---

## Executive Summary

**The Goal**: Transform RoboCo from infrastructure into a functioning AI company where 18 Claude Code instances work as structured employees, following strict processes with dual enforcement (blueprints guide, APIs enforce).

**The Problem**: We have APIs, models, and blueprints. But:
- No bridge between Claude Code and RoboCo APIs
- No enforcement of communication rules
- No enforcement of notification permissions
- No enforcement of task lifecycle
- No orchestration of multiple Claude Code instances
- No way to bootstrap the initial state

**The Solution**: Build the Agent Runtime - the layer that:
1. Spawns Claude Code instances with correct identities
2. Connects them to RoboCo APIs via MCP
3. Enforces ALL rules at the API level
4. Monitors health and resumes failed sessions
5. Drives workflow through event-based triggers

---

## Acceptance Criteria

### 1. MCP Server Integration
- [x] Claude Code can call Task API (claim, update status, complete) - `roboco/mcp/task_server.py`
- [x] Claude Code can call Message API (send/receive, channels) - `roboco/mcp/message_server.py`
- [x] Claude Code can call Notification API (PMs only) - `roboco/mcp/notify_server.py`
- [x] Claude Code can call Journal API (personal logs) - `roboco/mcp/journal_server.py`
- [x] MCP config file that any agent can use - Dynamic generation in orchestrator

### 2. API Rule Enforcement
- [x] Channel access enforced (reject writes to wrong channels) - `roboco/enforcement/channel_access.py`
- [x] Notification permissions enforced (only PM/Board/Auditor can notify) - `roboco/enforcement/notification_perms.py`, `api/routes/notifications.py`
- [x] Notification routing enforced (Cell PM can only notify own cell) - `roboco/enforcement/notification_perms.py`
- [x] Task state transitions enforced (no skipping states) - `roboco/enforcement/task_lifecycle.py`
- [x] Task ownership enforced (only assigned agent can update) - `roboco/enforcement/task_ownership.py`
- [x] Message validation enforced (must have type, task_id when applicable) - `api/routes/messages.py` (Pydantic validation)
- [x] Session boundaries enforced (auto-close, create new) - `api/routes/messages.py:297-306`
- [x] Handoff requirements enforced (can't close without docs) - `services/task.py:complete()`

### 3. Agent Orchestrator
- [x] Can spawn a Claude Code instance with a specific blueprint - `roboco/runtime/orchestrator.py`
- [x] Can pass MCP config to instance - `AgentOrchestrator._generate_mcp_config()`
- [x] Can monitor instance health (responsive, errored, stuck) - `AgentOrchestrator._health_loop()`
- [x] Can resume sessions on failure - `AgentOrchestrator.resolve_wait()` + auto-restart
- [x] Can gracefully shutdown all instances - `AgentOrchestrator.stop()`
- [x] Provides status dashboard/API - `AgentOrchestrator.get_status_summary()`

### 4. Agent Bootstrap
- [x] 18 agent records created in database - `roboco/bootstrap.py`
- [x] All channels created with correct membership - `bootstrap.py:create_channels()`
- [x] Silent observer (Auditor) configured for all channels - `bootstrap.py:AUDITOR_SILENT_ACCESS`
- [x] PM notification permissions configured - Built into enforcement layer
- [x] Initial channel messages posted - `bootstrap.py:create_initial_messages()`

### 5. Workflow Triggers
- [x] Task status changes trigger appropriate notifications - `roboco/events/handlers.py:handle_task_status_change()`
- [x] Session boundaries trigger new session creation - `roboco/events/handlers.py:handle_session_boundary()`
- [x] Handoff creation triggers documenter notification - `roboco/events/handlers.py:handle_handoff_created()`
- [x] QA pass/fail triggers appropriate next step - `roboco/events/handlers.py:handle_qa_result()`

### 6. End-to-End Validation
- [ ] Can spawn all 18 agents - Requires testing
- [ ] Agents can claim and work on tasks - Requires testing
- [ ] Dev → QA → Documenter handoff works - Requires testing
- [ ] Auditor can see all channels silently - Requires testing
- [ ] PM can send notifications, Dev cannot - Requires testing
- [ ] Invalid actions are rejected with clear errors - Requires testing

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CEO (You)                                       │
│                         Creates tasks, reviews work                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AGENT ORCHESTRATOR                                 │
│                                                                              │
│  • Spawns Claude Code instances with blueprints                              │
│  • Monitors health (heartbeat, error detection)                              │
│  • Resumes failed sessions                                                   │
│  • Provides management API                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
           ┌──────────────────────────┼──────────────────────────┐
           │                          │                          │
           ▼                          ▼                          ▼
    ┌─────────────┐            ┌─────────────┐            ┌─────────────┐
    │ Claude Code │            │ Claude Code │            │ Claude Code │
    │  (be-dev-1) │            │  (be-qa)    │            │  (be-pm)    │
    │             │            │             │            │             │
    │ Blueprint:  │            │ Blueprint:  │            │ Blueprint:  │
    │ be-dev.md   │            │ be-qa.md    │            │ be-pm.md    │
    └──────┬──────┘            └──────┬──────┘            └──────┬──────┘
           │                          │                          │
           └──────────────────────────┼──────────────────────────┘
                                      │
                                      ▼ MCP Protocol
┌─────────────────────────────────────────────────────────────────────────────┐
│                              MCP SERVERS                                     │
│                                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │  Task MCP   │  │ Message MCP │  │ Notify MCP  │  │ Journal MCP │         │
│  │             │  │             │  │             │  │             │         │
│  │ • claim     │  │ • send      │  │ • send      │  │ • write     │         │
│  │ • update    │  │ • read      │  │ • ack       │  │ • read      │         │
│  │ • complete  │  │ • channels  │  │ • list      │  │ • search    │         │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘         │
│         │                │                │                │                 │
│         └────────────────┴────────────────┴────────────────┘                 │
│                                      │                                       │
└──────────────────────────────────────┼───────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            ROBOCO API                                        │
│                     (FastAPI - Enforces ALL Rules)                           │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                        ENFORCEMENT LAYER                               │  │
│  │                                                                        │  │
│  │  • Channel Access      - Who can read/write which channels            │  │
│  │  • Notification Perms  - Only PM/Board/Auditor can notify             │  │
│  │  • Notification Route  - Cell PM → own cell only                      │  │
│  │  • Task State Machine  - Valid transitions only                       │  │
│  │  • Task Ownership      - Only assigned agent can modify               │  │
│  │  • Message Validation  - Type required, task_id when working          │  │
│  │  • Session Boundaries  - Auto-close at limits                         │  │
│  │  • Handoff Required    - No close without documentation               │  │
│  │  • Auditor Silent      - Sees all, not in member lists                │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │  Task API   │  │ Message API │  │ Notify API  │  │ Journal API │         │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                        ┌─────────────────────────┐
                        │       PostgreSQL        │
                        │    (Source of Truth)    │
                        └─────────────────────────┘
```

---

## Agent Lifecycle States

Agents don't run constantly - they're spawned on demand and terminated when waiting. This saves costs while maintaining responsiveness.

### State Diagram

```
                              ┌──────────────┐
                              │   OFFLINE    │
                              │ (no process) │
                              └──────┬───────┘
                                     │ spawn
                                     ▼
                              ┌──────────────┐
                              │   STARTING   │
                              │ (initializing)│
                              └──────┬───────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
                    ▼                ▼                ▼
             ┌──────────┐     ┌──────────┐     ┌──────────────┐
             │   IDLE   │     │  ACTIVE  │     │ WAITING_LONG │
             │(scanning)│◄───►│(working) │────►│ (terminated) │
             └────┬─────┘     └────┬─────┘     └──────┬───────┘
                  │                │                   │
                  │                ▼                   │ event
                  │         ┌──────────────┐          │ triggers
                  │         │WAITING_SHORT │          │ respawn
                  │         │  (polling)   │          │
                  │         └──────────────┘          │
                  │                                   │
                  └───────────────┬───────────────────┘
                                  │ stop
                                  ▼
                           ┌──────────────┐
                           │   STOPPING   │
                           │ (graceful)   │
                           └──────────────┘
```

### State Definitions

| State | Process Running? | Description |
|-------|-----------------|-------------|
| **OFFLINE** | No | Agent not running, no process exists |
| **STARTING** | Yes | Being spawned, initializing |
| **ACTIVE** | Yes | Working on a task, producing output |
| **WAITING_SHORT** | Yes | Polling for something (expected < 5 min) |
| **WAITING_LONG** | No | Terminated, orchestrator will respawn on event |
| **IDLE** | Yes | Running but no task, scanning for work |
| **STOPPING** | Yes | Graceful shutdown in progress |

### State Transitions

| From | To | Trigger |
|------|-----|---------|
| OFFLINE | STARTING | Orchestrator spawns agent (task assigned, event occurred) |
| STARTING | IDLE | Initialized with no assigned task |
| STARTING | ACTIVE | Initialized with resume prompt (had active task) |
| IDLE | ACTIVE | Agent claims task OR receives notification |
| ACTIVE | WAITING_SHORT | Blocked/question asked, expected quick resolution |
| ACTIVE | WAITING_LONG | Blocked with no ETA, submitted for QA, cross-cell wait |
| WAITING_SHORT | ACTIVE | Poll finds resolution |
| WAITING_LONG | STARTING | Event occurs (unblocked, QA result, etc.) |
| ACTIVE | IDLE | Task completed, no other assigned tasks |
| ANY | STOPPING | Graceful shutdown requested or session ending |
| ANY | OFFLINE | Process dies unexpectedly |

### Waiting Behavior

**Short waits (< 5 min expected):**
- Agent stays running
- Polls with exponential backoff
- Examples: waiting for PM answer in active conversation

**Long waits (> 5 min expected):**
- Agent saves checkpoint and terminates
- Orchestrator records what agent is waiting for
- On event, orchestrator respawns with resume context
- Examples: blocked on external dependency, awaiting QA review

#### Polling Backoff Algorithm

```python
class PollingBackoff:
    """Exponential backoff for WAITING_SHORT polling."""

    INITIAL_INTERVAL = 30      # Start at 30 seconds
    BACKOFF_MULTIPLIER = 1.5   # Multiply by 1.5 each time
    MAX_INTERVAL = 120         # Cap at 2 minutes
    MAX_TOTAL_WAIT = 300       # 5 minutes total before WAITING_LONG

    def __init__(self):
        self.current_interval = self.INITIAL_INTERVAL
        self.total_waited = 0

    def next_interval(self) -> int | None:
        """
        Get next polling interval.
        Returns None if should transition to WAITING_LONG.
        """
        if self.total_waited >= self.MAX_TOTAL_WAIT:
            return None  # Transition to WAITING_LONG

        interval = min(self.current_interval, self.MAX_INTERVAL)
        self.current_interval = int(self.current_interval * self.BACKOFF_MULTIPLIER)
        self.total_waited += interval

        return interval

# Polling sequence:
# Poll 1: 30s  (total: 30s)
# Poll 2: 45s  (total: 75s)
# Poll 3: 67s  (total: 142s)
# Poll 4: 101s (total: 243s)
# Poll 5: 120s (total: 363s) → exceeds 300s → WAITING_LONG
```

#### Wait Decision Logic

```python
async def decide_wait_type(context: WaitContext) -> str:
    """Decide whether to use short or long wait."""

    # Known short waits
    if context.waiting_for == "pm_answer" and context.pm_is_online:
        return "WAITING_SHORT"

    if context.waiting_for == "clarification" and context.asked_recently:
        return "WAITING_SHORT"

    # Known long waits
    if context.waiting_for == "qa_review":
        return "WAITING_LONG"  # QA might take hours

    if context.waiting_for == "blocker_resolution":
        if context.blocker_type == "external":
            return "WAITING_LONG"  # External deps are unpredictable
        if context.blocker_type == "internal":
            return "WAITING_SHORT"  # Internal might resolve quickly

    if context.waiting_for == "cross_cell":
        return "WAITING_LONG"  # Cross-cell coordination takes time

    # Default: start short, escalate to long after timeout
    return "WAITING_SHORT"
```

```python
class WaitingRecord:
    """Tracks what a WAITING_LONG agent is waiting for."""
    agent_id: str
    task_id: str
    waiting_for: str  # "blocker_resolution", "qa_result", "answer", "assignment"
    waiting_since: datetime
    context: dict  # Additional context for resume
```

### Resume Prompts

When orchestrator respawns a WAITING_LONG agent, it includes context about WHY:

```python
def generate_resume_prompt(record: WaitingRecord, event: Event) -> str:
    if record.waiting_for == "blocker_resolution":
        return f"""
You were working on TASK-{record.task_id} and got blocked.
The blocker has been resolved: {event.resolution_details}

Resume by:
1. Reading your checkpoint from .tasks/active/TASK-{record.task_id}/
2. Call roboco_task_unblock("{record.task_id}")
3. Continue from where you left off
"""
    elif record.waiting_for == "qa_result":
        if event.qa_passed:
            return "TASK passed QA. Return to scanning for new work."
        else:
            return f"TASK needs revision. QA notes: {event.qa_notes}"
    # ... etc
```

### Message Handling for Terminated Agents

When an agent is WAITING_LONG (terminated), they can't receive messages. Here's how we handle it:

#### Message Types and Handling

```
┌─────────────────────────────────────────────────────────────────────┐
│           MESSAGE HANDLING FOR WAITING_LONG AGENTS                  │
└─────────────────────────────────────────────────────────────────────┘

MESSAGE TYPE          │ HANDLING                        │ RESPAWN?
──────────────────────┼─────────────────────────────────┼──────────
Regular channel msg   │ Stored in channel normally      │ No
                      │ Agent reads history on respawn  │
                      │                                 │
@mention              │ Triggers immediate respawn      │ YES
                      │ with context: "You were         │
                      │ mentioned by X: {message}"      │
                      │                                 │
Direct notification   │ Handled by notification system  │ YES
                      │ Spawns agent with notification  │
                      │                                 │
Task assignment       │ Handled by task system          │ YES
                      │ Standard assignment flow        │
```

#### @Mention Detection

```python
class MentionDetector:
    """Detects @mentions and triggers agent respawn."""

    async def process_message(self, message: Message):
        mentions = self.extract_mentions(message.content)

        for agent_id in mentions:
            agent_state = await self.orchestrator.get_state(agent_id)

            if agent_state == "WAITING_LONG":
                # Agent is terminated - respawn with mention context
                await self.orchestrator.spawn_agent(
                    agent_id=agent_id,
                    initial_prompt=f"""
You were mentioned in #{message.channel_id} by {message.sender_id}:

"{message.content}"

Respond appropriately, then return to your previous task or scan for work.
"""
                )

    def extract_mentions(self, content: str) -> list[str]:
        """Extract @agent-id mentions from message content."""
        import re
        pattern = r'@([\w-]+)'
        return re.findall(pattern, content)
```

#### Channel History on Respawn

When agent respawns (for any reason), they can catch up on missed messages:

```python
async def get_catch_up_context(agent_id: str, since: datetime) -> str:
    """Get messages the agent missed while terminated."""
    agent = await get_agent(agent_id)

    # Get channels agent has access to
    channels = get_agent_channels(agent_id)

    missed_messages = []
    for channel_id in channels:
        messages = await get_messages(
            channel_id=channel_id,
            since=since,
            limit=50  # Last 50 messages per channel
        )
        if messages:
            missed_messages.append({
                "channel": channel_id,
                "messages": messages
            })

    if not missed_messages:
        return "No messages while you were away."

    return format_catch_up(missed_messages)
```

---

## Detailed Specifications

### 1. MCP Servers

We need to create MCP server wrappers for our RoboCo APIs so Claude Code can call them.

#### 1.1 Task MCP Server

```typescript
// Tools exposed to Claude Code
tools:
  - roboco_task_list          // List tasks (filtered by assignee, status, team)
  - roboco_task_get           // Get task details
  - roboco_task_claim         // Claim a task (validates state transition)
  - roboco_task_start         // Start working (claimed → in_progress)
  - roboco_task_update        // Update progress, notes
  - roboco_task_block         // Mark blocked with reason
  - roboco_task_unblock       // Unblock
  - roboco_task_submit_qa     // Submit for QA review
  - roboco_task_qa_pass       // QA passes task
  - roboco_task_qa_fail       // QA fails task (returns to dev)
  - roboco_task_submit_docs   // Submit for documentation
  - roboco_task_complete      // Complete task (validates requirements)
  - roboco_task_create_handoff // Create documenter handoff
```

#### 1.2 Message MCP Server

```typescript
tools:
  - roboco_channel_list       // List accessible channels
  - roboco_channel_messages   // Get messages from channel (validates access)
  - roboco_message_send       // Send message (validates channel access, type)
  - roboco_message_reply      // Reply to message
  - roboco_message_edit       // Edit own message
  - roboco_session_list       // List sessions
  - roboco_session_create     // Create new session (if boundary hit)
```

#### 1.3 Notification MCP Server

```typescript
tools:
  - roboco_notify_send        // Send notification (validates sender permission)
  - roboco_notify_list        // List pending notifications
  - roboco_notify_ack         // Acknowledge notification
  - roboco_notify_broadcast   // Broadcast (Board/Main PM only)
```

#### 1.4 Journal MCP Server

```typescript
tools:
  - roboco_journal_write      // Write journal entry
  - roboco_journal_read       // Read own journal
  - roboco_journal_search     // Search journal entries
```

#### 1.5 MCP Configuration File

```json
// .mcp-roboco.json
{
  "mcpServers": {
    "roboco-task": {
      "command": "python",
      "args": ["-m", "roboco.mcp.task_server"],
      "env": {
        "ROBOCO_API_URL": "http://localhost:8000",
        "ROBOCO_AGENT_ID": "${AGENT_ID}"
      }
    },
    "roboco-message": {
      "command": "python",
      "args": ["-m", "roboco.mcp.message_server"],
      "env": {
        "ROBOCO_API_URL": "http://localhost:8000",
        "ROBOCO_AGENT_ID": "${AGENT_ID}"
      }
    },
    "roboco-notify": {
      "command": "python",
      "args": ["-m", "roboco.mcp.notify_server"],
      "env": {
        "ROBOCO_API_URL": "http://localhost:8000",
        "ROBOCO_AGENT_ID": "${AGENT_ID}"
      }
    },
    "roboco-journal": {
      "command": "python",
      "args": ["-m", "roboco.mcp.journal_server"],
      "env": {
        "ROBOCO_API_URL": "http://localhost:8000",
        "ROBOCO_AGENT_ID": "${AGENT_ID}"
      }
    }
  }
}
```

---

### 2. API Enforcement Rules

Every rule from the blueprint must be enforced at the API level.

#### 2.1 Channel Access Enforcement

```python
# In channel routes/services

CHANNEL_ACCESS = {
    "backend-cell": {
        "read": ["be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc", "auditor"],
        "write": ["be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc"],
        "silent": ["auditor"]
    },
    "frontend-cell": {
        "read": ["fe-dev-1", "fe-dev-2", "fe-qa", "fe-pm", "fe-doc", "auditor"],
        "write": ["fe-dev-1", "fe-dev-2", "fe-qa", "fe-pm", "fe-doc"],
        "silent": ["auditor"]
    },
    "uxui-cell": {
        "read": ["ux-dev", "ux-qa", "ux-pm", "ux-doc", "auditor"],
        "write": ["ux-dev", "ux-qa", "ux-pm", "ux-doc"],
        "silent": ["auditor"]
    },
    "dev-all": {
        "read": ["be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev", "main-pm", "auditor"],
        "write": ["be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev"],
        "silent": ["auditor"]
    },
    "qa-all": {
        "read": ["be-qa", "fe-qa", "ux-qa", "main-pm", "auditor"],
        "write": ["be-qa", "fe-qa", "ux-qa"],
        "silent": ["auditor"]
    },
    "pm-all": {
        "read": ["be-pm", "fe-pm", "ux-pm", "main-pm", "auditor"],
        "write": ["be-pm", "fe-pm", "ux-pm", "main-pm"],
        "silent": ["auditor"]
    },
    "doc-all": {
        "read": ["be-doc", "fe-doc", "ux-doc", "main-pm", "auditor"],
        "write": ["be-doc", "fe-doc", "ux-doc"],
        "silent": ["auditor"]
    },
    "main-pm-board": {
        "read": ["main-pm", "product-owner", "head-marketing", "auditor", "ceo"],
        "write": ["main-pm", "product-owner", "head-marketing", "ceo"],
        "silent": ["auditor"]
    },
    "board-private": {
        "read": ["product-owner", "head-marketing", "auditor", "ceo"],
        "write": ["product-owner", "head-marketing", "ceo"],
        "silent": ["auditor"]
    },
    "announcements": {
        "read": ["*"],  # Everyone
        "write": ["product-owner", "head-marketing", "main-pm", "ceo"],
        "silent": ["auditor"]
    },
    "all-hands": {
        "read": ["*"],
        "write": ["*"],
        "silent": ["auditor"]
    }
}

def validate_channel_access(agent_id: str, channel_id: str, action: str) -> bool:
    """
    Validate agent can perform action on channel.

    Args:
        agent_id: The agent attempting access
        channel_id: The channel being accessed
        action: "read" or "write"

    Returns:
        True if allowed, False otherwise

    Raises:
        ChannelAccessDeniedError: If access denied
    """
    channel = CHANNEL_ACCESS.get(channel_id)
    if not channel:
        raise NotFoundError("Channel", channel_id)

    allowed = channel.get(action, [])
    if "*" in allowed or agent_id in allowed:
        return True

    # Silent observers can always read
    if action == "read" and agent_id in channel.get("silent", []):
        return True

    raise ChannelAccessDeniedError(
        agent_id=agent_id,
        channel_id=channel_id,
        action=action
    )
```

#### 2.2 Notification Permission Enforcement

```python
# Notification sender validation

NOTIFICATION_PERMISSIONS = {
    # Role -> who they can notify
    "cell_pm": lambda sender, recipient: same_cell(sender, recipient),
    "main_pm": lambda sender, recipient: is_pm(recipient) or True,  # Can notify anyone via escalation
    "product_owner": lambda sender, recipient: recipient in ["main-pm", "head-marketing", "auditor", "ceo"],
    "head_marketing": lambda sender, recipient: recipient in ["main-pm", "product-owner", "auditor", "ceo"],
    "auditor": lambda sender, recipient: True,  # Can notify anyone
    "ceo": lambda sender, recipient: True,  # Can notify anyone
    "developer": lambda sender, recipient: False,  # CANNOT notify
    "qa": lambda sender, recipient: False,  # CANNOT notify
    "documenter": lambda sender, recipient: False,  # CANNOT notify
}

def validate_notification_permission(sender_id: str, recipients: list[str]) -> bool:
    """
    Validate sender can notify recipients.

    Devs, QA, and Documenters CANNOT send notifications.
    Cell PMs can only notify their own cell.
    """
    sender = get_agent(sender_id)
    permission_check = NOTIFICATION_PERMISSIONS.get(sender.role)

    if permission_check is None:
        raise NotificationPermissionError(
            sender_id=sender_id,
            message="Unknown role"
        )

    for recipient_id in recipients:
        if not permission_check(sender_id, recipient_id):
            raise NotificationPermissionError(
                sender_id=sender_id,
                recipient_id=recipient_id,
                message=f"{sender.role} cannot notify {recipient_id}"
            )

    return True
```

#### 2.3 Task State Machine Enforcement

```python
# Valid task state transitions

VALID_TRANSITIONS = {
    "pending": ["claimed"],
    "claimed": ["in_progress", "pending"],  # Can unclaim
    "in_progress": ["blocked", "paused", "verifying"],
    "blocked": ["in_progress"],  # Unblock
    "paused": ["in_progress"],  # Resume
    "verifying": ["awaiting_qa", "needs_revision", "awaiting_documentation"],
    "needs_revision": ["in_progress"],  # Back to work
    "awaiting_qa": ["awaiting_documentation", "needs_revision"],  # QA pass/fail
    "awaiting_documentation": ["completed"],  # Only after docs done
    "completed": [],  # Terminal state
    "cancelled": [],  # Terminal state
}

def validate_task_transition(current: str, target: str) -> bool:
    """
    Validate task state transition is allowed.

    Raises:
        TaskLifecycleError: If transition is invalid
    """
    valid = VALID_TRANSITIONS.get(current, [])
    if target not in valid:
        raise TaskLifecycleError(
            current_status=current,
            target_status=target,
            message=f"Cannot transition from {current} to {target}. Valid: {valid}"
        )
    return True
```

#### 2.4 Task Ownership Enforcement

```python
def validate_task_ownership(agent_id: str, task_id: UUID, action: str) -> bool:
    """
    Validate agent can perform action on task.

    Rules:
    - Only assigned agent can update task status
    - Paused/interrupted tasks stay with original owner
    - PM can reassign (but not modify content)
    """
    task = get_task(task_id)

    # Task not assigned - can be claimed
    if task.assigned_to is None and action == "claim":
        return True

    # Task assigned to this agent
    if str(task.assigned_to) == agent_id:
        return True

    # PM can reassign
    agent = get_agent(agent_id)
    if agent.role in ["cell_pm", "main_pm"] and action == "reassign":
        if agent.role == "cell_pm" and task.team != agent.team:
            raise TaskError(
                task_id=task_id,
                message="Cell PM can only reassign tasks in their cell"
            )
        return True

    raise TaskError(
        task_id=task_id,
        message=f"Agent {agent_id} cannot {action} task owned by {task.assigned_to}"
    )
```

#### 2.5 Message Validation Enforcement

```python
def validate_message(agent_id: str, channel_id: str, message: MessageCreate) -> bool:
    """
    Validate message meets all requirements.

    Rules:
    - Must have valid type (reasoning, dialogue, decision, action, blocker, technical)
    - Must have task_id if agent is working on a task
    - Content length tracked for session boundaries
    """
    # Validate channel access
    validate_channel_access(agent_id, channel_id, "write")

    # Validate message type
    valid_types = ["reasoning", "dialogue", "decision", "action", "blocker", "technical"]
    if message.type not in valid_types:
        raise ValidationError(
            field="type",
            message=f"Invalid message type. Must be one of: {valid_types}"
        )

    # If agent has active task, message should reference it
    agent = get_agent(agent_id)
    if agent.current_task_id and not message.task_id:
        # Warning, not error - but logged
        logger.warning(
            "Message without task_id from agent with active task",
            agent_id=agent_id,
            task_id=str(agent.current_task_id)
        )

    return True
```

#### 2.6 Session Boundary Enforcement

```python
async def check_session_boundaries(session_id: UUID, new_message: Message) -> Session:
    """
    Check if session boundaries are exceeded and handle accordingly.

    Boundaries:
    - max_time_window: Maximum duration
    - max_message_count: Maximum messages
    - max_content_length: Maximum characters
    - timeout_seconds: Inactivity timeout
    """
    session = await get_session(session_id)

    now = datetime.utcnow()

    # Check time window
    if session.max_time_window:
        elapsed = now - session.started_at
        if elapsed > session.max_time_window:
            session = await close_session(session_id, reason="time_window_exceeded")
            session = await create_new_session(session.group_id)
            return session

    # Check message count
    if session.max_message_count:
        if session.message_count >= session.max_message_count:
            session = await close_session(session_id, reason="message_count_exceeded")
            session = await create_new_session(session.group_id)
            return session

    # Check content length
    if session.max_content_length:
        new_total = session.total_content_length + len(new_message.content)
        if new_total > session.max_content_length:
            session = await close_session(session_id, reason="content_length_exceeded")
            session = await create_new_session(session.group_id)
            return session

    # Check inactivity timeout
    if session.timeout_seconds:
        inactive = (now - session.last_activity_at).total_seconds()
        if inactive > session.timeout_seconds:
            session = await close_session(session_id, reason="timeout")
            session = await create_new_session(session.group_id)
            return session

    return session
```

#### 2.7 Handoff Requirements Enforcement

```python
async def validate_task_completion(task_id: UUID) -> bool:
    """
    Validate task can be completed.

    Requirements:
    - Must have dev notes
    - Must have QA notes (if QA reviewed)
    - Must have documentation (handoff completed)
    - All acceptance criteria checked
    """
    task = await get_task(task_id)

    errors = []

    # Dev notes required
    if not task.dev_notes:
        errors.append("Missing dev journey notes")

    # QA notes required if QA reviewed
    if task.status == "awaiting_documentation" and not task.qa_notes:
        errors.append("Missing QA notes")

    # Handoff required
    if not task.documenter_handoff:
        errors.append("Missing documenter handoff")

    # Documentation required
    if not task.final_documentation:
        errors.append("Missing final documentation")

    # All acceptance criteria must be checked
    unchecked = [ac for ac in task.acceptance_criteria if not ac.checked]
    if unchecked:
        errors.append(f"Unchecked acceptance criteria: {len(unchecked)}")

    if errors:
        raise ValidationError(
            field="completion",
            message=f"Task cannot be completed: {'; '.join(errors)}"
        )

    return True
```

#### 2.8 Concurrent Task Limits

```python
class AgentTaskSlots:
    """
    Track an agent's task portfolio.

    Rule: Agent can have ONE active task, but multiple waiting tasks.
    Must pause/complete current before claiming new.
    """
    active_task: UUID | None  # Currently executing (in_progress)
    waiting_tasks: list[UUID]  # Blocked, paused, awaiting_*

    def can_claim_new(self) -> bool:
        """Check if agent can claim a new task."""
        return self.active_task is None

    def claim(self, task_id: UUID):
        """Claim a task."""
        if self.active_task is not None:
            raise TaskError(
                message="Already have active task. Pause or complete it first."
            )
        self.active_task = task_id

    def move_to_waiting(self, task_id: UUID):
        """Move task to waiting (blocked/paused/submitted)."""
        if self.active_task == task_id:
            self.active_task = None
            self.waiting_tasks.append(task_id)

    def resume(self, task_id: UUID):
        """Resume a waiting task."""
        if self.active_task is not None:
            raise TaskError(
                message="Must pause active task before resuming another."
            )
        if task_id not in self.waiting_tasks:
            raise TaskError(message="Task not in waiting list.")
        self.waiting_tasks.remove(task_id)
        self.active_task = task_id


async def validate_task_claim(agent_id: str, task_id: str) -> bool:
    """Validate agent can claim this task."""
    agent = await get_agent(agent_id)

    # Check if agent already has an active task
    if agent.current_task_id is not None:
        current = await get_task(agent.current_task_id)

        # Allow if current task is in a waiting state
        waiting_states = ["blocked", "paused", "awaiting_qa", "awaiting_documentation"]
        if current.status not in waiting_states:
            raise TaskError(
                message=f"Agent {agent_id} already working on {agent.current_task_id}. "
                        f"Complete or pause it before claiming new task."
            )

    return True
```

#### 2.9 Task Reassignment Enforcement

```python
async def reassign_task(
    task_id: str,
    from_agent: str,
    to_agent: str,
    pm_id: str,
    reason: str
) -> Task:
    """
    PM reassigns task from one agent to another.

    Rules:
    - Only PMs can reassign
    - Cell PM can only reassign within cell
    - New agent inherits checkpoint, continues from there
    """
    # Validate PM has permission
    pm = await get_agent(pm_id)
    if pm.role not in ["cell_pm", "main_pm"]:
        raise PermissionDeniedError("Only PMs can reassign tasks")

    task = await get_task(task_id)

    # Cell PM can only reassign within cell
    if pm.role == "cell_pm" and task.team != pm.team:
        raise PermissionDeniedError("Cell PM can only reassign within cell")

    # Validate target agent is same role type
    from_agent_obj = await get_agent(from_agent)
    to_agent_obj = await get_agent(to_agent)
    if from_agent_obj.role != to_agent_obj.role:
        raise ValidationError(
            message=f"Cannot reassign from {from_agent_obj.role} to {to_agent_obj.role}"
        )

    # Document the reassignment
    task.reassignment_history.append({
        "from": from_agent,
        "to": to_agent,
        "by": pm_id,
        "reason": reason,
        "at": datetime.utcnow()
    })

    # Update assignment
    task.assigned_to = to_agent

    await save_task(task)

    # Notify new agent with context
    await create_notification(
        type="task_assignment",
        from_agent=pm_id,
        to_agents=[to_agent],
        subject=f"Reassigned: {task.title}",
        body=f"""
TASK-{task.id} has been reassigned to you from {from_agent}.

Reason: {reason}

IMPORTANT: This is a task in progress. Check:
1. .tasks/active/TASK-{task.id}/ for current state
2. journal.md for previous agent's notes
3. plan.md for the approach
4. Any checkpoints saved

Continue from where they left off, don't restart from scratch.
""",
        requires_ack=True
    )

    # Log for audit
    await log_event(
        event_type="task_reassigned",
        task_id=task_id,
        details={
            "from": from_agent,
            "to": to_agent,
            "by": pm_id,
            "reason": reason
        }
    )

    return task
```

---

### 3. Agent Orchestrator

The orchestrator manages Claude Code instances.

#### 3.1 Orchestrator Core

```python
# roboco/runtime/orchestrator.py

class AgentOrchestrator:
    """
    Manages Claude Code instances for all agents.

    Responsibilities:
    - Spawn agents with correct blueprints
    - Monitor health (heartbeat, errors)
    - Resume failed sessions
    - Provide status API
    """

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self._instances: dict[str, AgentInstance] = {}
        self._health_task: asyncio.Task | None = None
        self._running = False

    async def spawn_agent(
        self,
        agent_id: str,
        blueprint_path: str,
        initial_prompt: str | None = None
    ) -> AgentInstance:
        """
        Spawn a Claude Code instance for an agent.

        Args:
            agent_id: Agent identifier (e.g., "be-dev-1")
            blueprint_path: Path to blueprint markdown file
            initial_prompt: Optional initial prompt

        Returns:
            AgentInstance handle
        """
        # Load blueprint
        blueprint = await self._load_blueprint(blueprint_path)

        # Generate MCP config with agent ID
        mcp_config = await self._generate_mcp_config(agent_id)

        # Build command
        cmd = [
            "claude",
            "--system-prompt-file", blueprint_path,
            "--mcp-config", mcp_config,
            "--output-format", "stream-json",
        ]

        if initial_prompt:
            cmd.extend(["-p", initial_prompt])

        # Spawn process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "ROBOCO_AGENT_ID": agent_id,
            }
        )

        instance = AgentInstance(
            agent_id=agent_id,
            process=process,
            started_at=datetime.utcnow(),
            blueprint_path=blueprint_path,
        )

        self._instances[agent_id] = instance

        # Start output monitoring
        asyncio.create_task(self._monitor_output(instance))

        logger.info("Agent spawned", agent_id=agent_id, pid=process.pid)

        return instance

    async def spawn_all_agents(self) -> list[AgentInstance]:
        """Spawn all 18 agents."""
        agents = await get_all_agents()
        instances = []

        for agent in agents:
            instance = await self.spawn_agent(
                agent_id=agent.slug,
                blueprint_path=f"agents/blueprints/{agent.team}/{agent.slug}.md",
                initial_prompt=self._get_initial_prompt(agent)
            )
            instances.append(instance)

        return instances

    async def stop_agent(self, agent_id: str, graceful: bool = True) -> None:
        """Stop an agent instance."""
        instance = self._instances.get(agent_id)
        if not instance:
            return

        if graceful:
            # Send SIGTERM, wait for graceful shutdown
            instance.process.terminate()
            try:
                await asyncio.wait_for(instance.process.wait(), timeout=30)
            except asyncio.TimeoutError:
                instance.process.kill()
        else:
            instance.process.kill()

        del self._instances[agent_id]
        logger.info("Agent stopped", agent_id=agent_id)

    async def restart_agent(self, agent_id: str) -> AgentInstance:
        """Restart a failed or stuck agent."""
        instance = self._instances.get(agent_id)

        if instance:
            await self.stop_agent(agent_id)

        # Get agent info from DB
        agent = await get_agent_by_slug(agent_id)

        # Respawn with resume prompt if had active task
        resume_prompt = None
        if agent.current_task_id:
            task = await get_task(agent.current_task_id)
            resume_prompt = f"""
You were working on TASK-{task.id}: {task.title}

Resume your work by:
1. Reading the task record at .tasks/active/TASK-{task.id}/
2. Restoring context from README.md, plan.md, journal.md
3. Adding a journal entry: "Resuming task after session restart"
4. Continuing from where you left off
"""

        return await self.spawn_agent(
            agent_id=agent_id,
            blueprint_path=f"agents/blueprints/{agent.team}/{agent.slug}.md",
            initial_prompt=resume_prompt
        )

    async def _health_check_loop(self) -> None:
        """Periodically check agent health."""
        while self._running:
            for agent_id, instance in list(self._instances.items()):
                # Check if process still running
                if instance.process.returncode is not None:
                    logger.warning(
                        "Agent process exited",
                        agent_id=agent_id,
                        returncode=instance.process.returncode
                    )
                    # Auto-restart
                    await self.restart_agent(agent_id)
                    continue

                # Check for activity (no output in X minutes = stuck)
                if instance.last_activity:
                    inactive = (datetime.utcnow() - instance.last_activity).total_seconds()
                    if inactive > self.config.stuck_threshold_seconds:
                        logger.warning(
                            "Agent appears stuck",
                            agent_id=agent_id,
                            inactive_seconds=inactive
                        )
                        # Could restart or alert

            await asyncio.sleep(self.config.health_check_interval_seconds)

    def get_status(self) -> dict:
        """Get orchestrator status."""
        return {
            "running": self._running,
            "total_agents": len(self._instances),
            "agents": [
                {
                    "agent_id": inst.agent_id,
                    "pid": inst.process.pid,
                    "status": "running" if inst.process.returncode is None else "stopped",
                    "started_at": inst.started_at.isoformat(),
                    "last_activity": inst.last_activity.isoformat() if inst.last_activity else None,
                }
                for inst in self._instances.values()
            ]
        }
```

#### 3.2 Agent Instance Model

```python
@dataclass
class AgentInstance:
    """Represents a running Claude Code instance."""
    agent_id: str
    process: asyncio.subprocess.Process
    started_at: datetime
    blueprint_path: str
    session_id: str | None = None
    last_activity: datetime | None = None
    error_count: int = 0
    output_buffer: list[str] = field(default_factory=list)
```

---

### 4. Agent Bootstrap

Create the initial state for the system.

#### 4.1 Bootstrap Script

```python
# roboco/runtime/bootstrap.py

async def bootstrap_roboco() -> None:
    """
    Bootstrap the RoboCo system with all agents, channels, and initial state.
    """
    logger.info("Starting RoboCo bootstrap...")

    # 1. Create all agents
    await create_agents()

    # 2. Create all channels
    await create_channels()

    # 3. Configure permissions
    await configure_permissions()

    # 4. Post initial messages
    await post_initial_messages()

    logger.info("Bootstrap complete!")


async def create_agents() -> list[Agent]:
    """Create all 18 agents in the database."""

    agents = [
        # Board
        {"slug": "product-owner", "name": "Product Owner", "role": "product_owner", "team": None, "can_notify": True},
        {"slug": "head-marketing", "name": "Head of Marketing", "role": "head_marketing", "team": None, "can_notify": True},
        {"slug": "auditor", "name": "Auditor", "role": "auditor", "team": None, "can_notify": True},

        # Management
        {"slug": "main-pm", "name": "Main PM", "role": "main_pm", "team": None, "can_notify": True},

        # Backend Cell
        {"slug": "be-dev-1", "name": "Backend Developer 1", "role": "developer", "team": "backend", "can_notify": False},
        {"slug": "be-dev-2", "name": "Backend Developer 2", "role": "developer", "team": "backend", "can_notify": False},
        {"slug": "be-qa", "name": "Backend QA", "role": "qa", "team": "backend", "can_notify": False},
        {"slug": "be-pm", "name": "Backend PM", "role": "cell_pm", "team": "backend", "can_notify": True},
        {"slug": "be-doc", "name": "Backend Documenter", "role": "documenter", "team": "backend", "can_notify": False},

        # Frontend Cell
        {"slug": "fe-dev-1", "name": "Frontend Developer 1", "role": "developer", "team": "frontend", "can_notify": False},
        {"slug": "fe-dev-2", "name": "Frontend Developer 2", "role": "developer", "team": "frontend", "can_notify": False},
        {"slug": "fe-qa", "name": "Frontend QA", "role": "qa", "team": "frontend", "can_notify": False},
        {"slug": "fe-pm", "name": "Frontend PM", "role": "cell_pm", "team": "frontend", "can_notify": True},
        {"slug": "fe-doc", "name": "Frontend Documenter", "role": "documenter", "team": "frontend", "can_notify": False},

        # UX/UI Cell
        {"slug": "ux-dev", "name": "UX/UI Developer", "role": "developer", "team": "ux_ui", "can_notify": False},
        {"slug": "ux-qa", "name": "UX/UI QA", "role": "qa", "team": "ux_ui", "can_notify": False},
        {"slug": "ux-pm", "name": "UX/UI PM", "role": "cell_pm", "team": "ux_ui", "can_notify": True},
        {"slug": "ux-doc", "name": "UX/UI Documenter", "role": "documenter", "team": "ux_ui", "can_notify": False},
    ]

    created = []
    for agent_data in agents:
        # Load blueprint as system prompt
        blueprint_path = get_blueprint_path(agent_data["slug"], agent_data.get("team"))
        system_prompt = await load_blueprint(blueprint_path)

        agent = await create_agent(
            **agent_data,
            system_prompt=system_prompt,
            model="claude-sonnet-4-20250514",  # Default model
        )
        created.append(agent)
        logger.info("Created agent", slug=agent.slug, role=agent.role)

    return created


async def create_channels() -> list[Channel]:
    """Create all channels with proper membership."""

    channels = [
        # Cell channels
        {
            "slug": "backend-cell",
            "name": "#backend-cell",
            "type": "cell",
            "members": ["be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc"],
            "writers": ["be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc"],
            "silent_observers": ["auditor"],
        },
        {
            "slug": "frontend-cell",
            "name": "#frontend-cell",
            "type": "cell",
            "members": ["fe-dev-1", "fe-dev-2", "fe-qa", "fe-pm", "fe-doc"],
            "writers": ["fe-dev-1", "fe-dev-2", "fe-qa", "fe-pm", "fe-doc"],
            "silent_observers": ["auditor"],
        },
        {
            "slug": "uxui-cell",
            "name": "#uxui-cell",
            "type": "cell",
            "members": ["ux-dev", "ux-qa", "ux-pm", "ux-doc"],
            "writers": ["ux-dev", "ux-qa", "ux-pm", "ux-doc"],
            "silent_observers": ["auditor"],
        },

        # Cross-cell channels
        {
            "slug": "dev-all",
            "name": "#dev-all",
            "type": "cross_cell",
            "members": ["be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev", "main-pm"],
            "writers": ["be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev"],
            "silent_observers": ["auditor"],
        },
        {
            "slug": "qa-all",
            "name": "#qa-all",
            "type": "cross_cell",
            "members": ["be-qa", "fe-qa", "ux-qa", "main-pm"],
            "writers": ["be-qa", "fe-qa", "ux-qa"],
            "silent_observers": ["auditor"],
        },
        {
            "slug": "pm-all",
            "name": "#pm-all",
            "type": "cross_cell",
            "members": ["be-pm", "fe-pm", "ux-pm", "main-pm"],
            "writers": ["be-pm", "fe-pm", "ux-pm", "main-pm"],
            "silent_observers": ["auditor"],
        },
        {
            "slug": "doc-all",
            "name": "#doc-all",
            "type": "cross_cell",
            "members": ["be-doc", "fe-doc", "ux-doc", "main-pm"],
            "writers": ["be-doc", "fe-doc", "ux-doc"],
            "silent_observers": ["auditor"],
        },

        # Management channels
        {
            "slug": "main-pm-board",
            "name": "#main-pm-board",
            "type": "management",
            "members": ["main-pm", "product-owner", "head-marketing"],
            "writers": ["main-pm", "product-owner", "head-marketing"],
            "silent_observers": ["auditor"],
        },
        {
            "slug": "board-private",
            "name": "#board-private",
            "type": "management",
            "members": ["product-owner", "head-marketing"],
            "writers": ["product-owner", "head-marketing"],
            "silent_observers": ["auditor"],
        },

        # Special channels
        {
            "slug": "announcements",
            "name": "#announcements",
            "type": "special",
            "members": ["*"],  # Everyone
            "writers": ["product-owner", "head-marketing", "main-pm"],
            "silent_observers": ["auditor"],
        },
        {
            "slug": "all-hands",
            "name": "#all-hands",
            "type": "special",
            "members": ["*"],
            "writers": ["*"],
            "silent_observers": ["auditor"],
        },
    ]

    created = []
    for channel_data in channels:
        channel = await create_channel(**channel_data)
        created.append(channel)
        logger.info("Created channel", slug=channel.slug)

    return created
```

---

### 5. Multi-Project Support

RoboCo manages multiple projects. Agents need to know which project they're working on.

#### 5.1 Project Model

```python
class Project:
    """A project/repository managed by RoboCo."""
    id: UUID
    name: str
    slug: str  # "fastapi-guard", "roboco", etc.
    repo_url: str  # Git remote
    local_path: str  # Where it's checked out on disk

    # Technology
    tech_stack: str  # "python", "typescript", "mixed"
    cell: Team  # Primary responsible cell

    # Commands (project-specific)
    test_command: str  # "uv run pytest"
    lint_command: str  # "uv run ruff check ."
    typecheck_command: str | None  # "uv run mypy src/"
    build_command: str | None

    # Paths
    docs_path: str  # "./docs"
    tasks_path: str  # "./.tasks"
    src_path: str  # "./src"

    # Git settings
    default_branch: str  # "main"
    pr_required: bool  # Require PR for changes?
```

#### 5.2 Task-Project Relationship

```python
class Task:
    ...
    project_id: UUID  # Which project this task is for
    ...
```

#### 5.3 Project Context in MCP Responses

When agent claims a task, MCP returns project context:

```json
{
  "status": "claimed",
  "task": { ... },
  "project": {
    "name": "fastapi-guard",
    "slug": "fastapi-guard",
    "local_path": "/projects/fastapi-guard",
    "tech_stack": "python",
    "test_command": "uv run pytest",
    "lint_command": "uv run ruff check .",
    "typecheck_command": "uv run mypy src/",
    "docs_path": "./docs",
    "default_branch": "main"
  },
  "next_step": "UNDERSTAND",
  "guidance": "..."
}
```

#### 5.4 Cross-Project Tasks

Tasks spanning multiple projects are broken into sub-tasks by Main PM:

```
EPIC: Update shared library and consumers
├─ TASK-100: Update shared-utils (project: shared-utils, cell: backend)
├─ TASK-101: Update fastapi-guard to use new version (project: fastapi-guard, cell: backend)
└─ TASK-102: Update web-app to use new version (project: web-app, cell: frontend)
```

Each sub-task is assigned to appropriate cell based on project's primary cell.

---

### 6. Workflow Triggers

Events that automatically trigger the next step in the workflow.

#### 6.1 Event System

```python
# roboco/runtime/events.py

class WorkflowEventHandler:
    """Handles workflow events and triggers appropriate actions."""

    async def on_task_status_changed(
        self,
        task_id: UUID,
        old_status: str,
        new_status: str,
        agent_id: str
    ) -> None:
        """Handle task status change."""

        task = await get_task(task_id)

        # Dev submits for QA
        if new_status == "awaiting_qa":
            await self._notify_qa_review_needed(task, agent_id)

        # QA passes
        elif old_status == "awaiting_qa" and new_status == "awaiting_documentation":
            await self._notify_documentation_needed(task, agent_id)

        # QA fails
        elif old_status == "awaiting_qa" and new_status == "needs_revision":
            await self._notify_revision_needed(task, agent_id)

        # Task blocked
        elif new_status == "blocked":
            await self._notify_blocked(task, agent_id)

        # Task completed
        elif new_status == "completed":
            await self._notify_completed(task, agent_id)

    async def _notify_qa_review_needed(self, task: Task, submitter_id: str) -> None:
        """Notify QA that a task is ready for review."""
        # Find the PM for the task's team
        pm = await get_cell_pm(task.team)
        qa = await get_cell_qa(task.team)

        # PM sends notification to QA (enforced by permissions)
        await create_notification(
            type="review_request",
            from_agent=pm.id,
            to_agents=[qa.id],
            subject=f"Review needed: TASK-{task.id}",
            body=f"""
{submitter_id} has submitted TASK-{task.id} for QA review.

**Task**: {task.title}
**Submitted by**: {submitter_id}

Please review and either approve or request revisions.
""",
            related_task=task.id,
            requires_ack=True,
        )

    async def _notify_documentation_needed(self, task: Task, qa_id: str) -> None:
        """Notify Documenter that a task needs documentation."""
        pm = await get_cell_pm(task.team)
        documenter = await get_cell_documenter(task.team)

        await create_notification(
            type="documentation_request",
            from_agent=pm.id,
            to_agents=[documenter.id],
            subject=f"Documentation needed: TASK-{task.id}",
            body=f"""
TASK-{task.id} has passed QA and needs documentation.

**Task**: {task.title}
**QA passed by**: {qa_id}
**Handoff location**: .tasks/active/TASK-{task.id}/handoff.md

Please review the handoff notes and create the necessary documentation.
""",
            related_task=task.id,
            requires_ack=True,
        )

    async def _notify_revision_needed(self, task: Task, qa_id: str) -> None:
        """Notify Dev that QA failed the task."""
        pm = await get_cell_pm(task.team)
        dev = await get_agent(task.assigned_to)

        await create_notification(
            type="priority_change",  # Revision is priority
            from_agent=pm.id,
            to_agents=[dev.id],
            subject=f"Revision needed: TASK-{task.id}",
            body=f"""
TASK-{task.id} did not pass QA review and needs revision.

**Task**: {task.title}
**QA notes**: See task record
**Reviewed by**: {qa_id}

Please address the QA feedback and resubmit.
""",
            related_task=task.id,
            requires_ack=True,
        )
```

---

### 7. Auditor Operating Model

The Auditor is fundamentally different from other agents - observation-driven rather than task-driven.

#### 7.1 Auditor Modes

```
┌─────────────────────────────────────────────────────────────────────┐
│                     AUDITOR OPERATING MODES                         │
└─────────────────────────────────────────────────────────────────────┘

1. PASSIVE MONITORING (continuous)
   ├─ Subscribe to all channel message streams
   ├─ Receive all messages in real-time
   ├─ Apply heuristics to detect issues:
   │   ├─ Agent stuck (no progress in 2+ hours)
   │   ├─ Communication breakdown (question unanswered)
   │   ├─ Quality concern (commit without tests mentioned)
   │   └─ Process violation (skipped steps)
   └─ Flag items for CEO review

2. ACTIVE AUDIT (triggered)
   ├─ Trigger: schedule (daily/weekly), CEO request, flag threshold
   ├─ Deep dive into specific area:
   │   ├─ Code quality audit: review recent commits
   │   ├─ Documentation audit: check completeness
   │   └─ Process compliance: verify task lifecycle followed
   └─ Produce detailed report

3. SPOT CHECK (random)
   ├─ Randomly select completed tasks
   ├─ Verify: tests exist, docs exist, QA actually tested
   └─ Catch agents cutting corners
```

#### 7.2 Auditor Implementation

```python
class AuditorAgent:
    """
    Unlike other agents, Auditor runs in monitoring mode
    with periodic active audit cycles.
    """

    async def run(self):
        # Start monitoring all channels
        monitoring_task = asyncio.create_task(self.passive_monitor())

        # Schedule periodic audits
        audit_task = asyncio.create_task(self.audit_loop())

        await asyncio.gather(monitoring_task, audit_task)

    async def passive_monitor(self):
        """Subscribe to all channels, flag issues in real-time."""
        channels = await get_all_channels()

        for channel in channels:
            asyncio.create_task(self.monitor_channel(channel))

    async def monitor_channel(self, channel):
        """Watch a single channel for issues."""
        async for message in subscribe_to_channel(channel.id):
            await self.analyze_message(message)

    async def analyze_message(self, message):
        """Apply heuristics to detect issues."""
        # Check for blockers unaddressed > 24 hours
        if message.type == "blocker":
            await self.track_blocker(message)

        # Check for questions unanswered > 2 hours
        if "?" in message.content and message.type == "dialogue":
            await self.track_question(message)

        # Detect anomalies
        if await self.detect_anomaly(message):
            await self.create_flag(message, severity="warning")

    async def audit_loop(self):
        """Run periodic audits."""
        while True:
            await asyncio.sleep(AUDIT_INTERVAL)  # e.g., 6 hours

            report = await self.run_audit()
            await self.send_report_to_ceo(report)
```

#### 7.3 Flagging System

```python
class AuditorFlag:
    id: UUID
    severity: str  # "info", "warning", "concern", "critical"
    category: str  # "quality", "process", "communication", "security"
    subject: str  # What/who is flagged
    description: str
    evidence: list[str]  # Message IDs, commit refs, etc.
    created_at: datetime
    reviewed_by_ceo: bool
    ceo_action: str | None  # What CEO decided

# Severity definitions:
# - info: FYI, no action needed
# - warning: Should look at this soon
# - concern: Needs attention within 24 hours
# - critical: Needs immediate attention (security, production issue)
```

#### 7.4 CEO Interaction

- CEO has dashboard showing flags
- CEO can: dismiss, acknowledge, request investigation
- Auditor can be asked to investigate further
- CEO can request Auditor to notify someone (Auditor has that power)

#### 7.5 Detection Heuristics

Specific rules for what the Auditor flags:

```python
class AuditorHeuristics:
    """Detection rules for the Auditor."""

    # =========================================================================
    # STUCK DETECTION
    # =========================================================================

    STUCK_RULES = {
        "no_commits": {
            "condition": "Task in_progress with no commits for 2+ hours",
            "severity": "warning",
            "category": "progress",
        },
        "no_messages": {
            "condition": "Task in_progress with no agent messages for 1+ hour",
            "severity": "info",
            "category": "progress",
        },
        "no_plan": {
            "condition": "Task claimed but no plan submitted within 30 min",
            "severity": "warning",
            "category": "process",
        },
        "blocked_too_long": {
            "condition": "Task blocked for 24+ hours without resolution",
            "severity": "concern",
            "category": "progress",
        },
    }

    # =========================================================================
    # COMMUNICATION ISSUES
    # =========================================================================

    COMMUNICATION_RULES = {
        "unanswered_question": {
            "condition": "Question (contains '?') unanswered for 2+ hours",
            "severity": "warning",
            "category": "communication",
        },
        "blocker_no_pm_response": {
            "condition": "Blocker reported with no PM response for 1+ hour",
            "severity": "warning",
            "category": "communication",
        },
        "cross_cell_no_ack": {
            "condition": "Cross-cell request with no acknowledgment for 4+ hours",
            "severity": "concern",
            "category": "communication",
        },
        "notification_not_acked": {
            "condition": "Required-ack notification not acknowledged for 4+ hours",
            "severity": "warning",
            "category": "communication",
        },
    }

    # =========================================================================
    # QUALITY CONCERNS
    # =========================================================================

    QUALITY_RULES = {
        "no_tests_mentioned": {
            "condition": "Task submitted for QA without 'test' in commit messages",
            "severity": "info",
            "category": "quality",
        },
        "thin_documentation": {
            "condition": "Documentation marked complete but < 200 words",
            "severity": "warning",
            "category": "quality",
        },
        "multiple_qa_rejections": {
            "condition": "Same task rejected by QA 3+ times",
            "severity": "concern",
            "category": "quality",
        },
        "no_journey_notes": {
            "condition": "Task completed without dev journey notes",
            "severity": "warning",
            "category": "quality",
        },
    }

    # =========================================================================
    # PROCESS VIOLATIONS
    # =========================================================================

    PROCESS_RULES = {
        "skipped_qa": {
            "condition": "Task moved to completed without awaiting_qa state",
            "severity": "critical",
            "category": "process",
        },
        "commit_without_task": {
            "condition": "Commit made by agent without active task",
            "severity": "warning",
            "category": "process",
        },
        "wrong_channel": {
            "condition": "Agent attempted to post in unauthorized channel",
            "severity": "warning",
            "category": "process",
        },
        "notification_by_dev": {
            "condition": "Dev/QA/Doc attempted to send notification",
            "severity": "info",
            "category": "process",
        },
        "reassign_without_reason": {
            "condition": "Task reassigned without documented reason",
            "severity": "warning",
            "category": "process",
        },
    }

    # =========================================================================
    # SECURITY
    # =========================================================================

    SECURITY_RULES = {
        "many_failed_calls": {
            "condition": "Agent has 10+ failed API calls in 5 minutes",
            "severity": "concern",
            "category": "security",
        },
        "restricted_channel_attempt": {
            "condition": "Agent attempted access to #board-private or #main-pm-board",
            "severity": "concern",
            "category": "security",
        },
        "potential_secret_exposure": {
            "condition": "Message/commit contains pattern matching secrets",
            "severity": "critical",
            "category": "security",
            "patterns": [
                r"api[_-]?key\s*[=:]\s*['\"][^'\"]+['\"]",
                r"password\s*[=:]\s*['\"][^'\"]+['\"]",
                r"secret\s*[=:]\s*['\"][^'\"]+['\"]",
                r"token\s*[=:]\s*['\"][^'\"]+['\"]",
            ],
        },
    }
```

#### 7.6 Spot Check Protocol

Random verification of completed work:

```python
class SpotCheckProtocol:
    """Random audits of completed tasks."""

    SAMPLE_RATE = 0.2  # Check 20% of completed tasks

    async def run_spot_check(self):
        """Randomly select and verify completed tasks."""
        recent_completed = await get_tasks(
            status="completed",
            completed_after=datetime.utcnow() - timedelta(days=7)
        )

        sample = random.sample(
            recent_completed,
            k=int(len(recent_completed) * self.SAMPLE_RATE)
        )

        for task in sample:
            issues = await self.verify_task(task)
            if issues:
                await self.create_spot_check_flag(task, issues)

    async def verify_task(self, task: Task) -> list[str]:
        """Verify task meets all completion requirements."""
        issues = []

        # Check tests exist
        if not await self.verify_tests_exist(task):
            issues.append("No tests found for task")

        # Check documentation exists
        if not await self.verify_docs_exist(task):
            issues.append("Documentation incomplete or missing")

        # Check QA actually reviewed (not just rubber-stamped)
        if not await self.verify_qa_thoroughness(task):
            issues.append("QA review appears superficial")

        # Check journey notes have substance
        if not await self.verify_journey_notes(task):
            issues.append("Journey notes lack detail")

        # Check acceptance criteria actually verified
        if not await self.verify_acceptance_criteria(task):
            issues.append("Acceptance criteria not properly verified")

        return issues
```

---

### 8. PM Operating Model

PMs (Cell PMs and Main PM) operate differently from task-executing agents. They're "always aware" but not "always thinking."

#### 8.1 PM Operating Loop

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CELL PM OPERATING LOOP                          │
└─────────────────────────────────────────────────────────────────────┘

1. EVENT TRIGGERS (immediate response):
   ├─ New task assigned to cell → Triage and assign to dev
   ├─ Agent blocked → Evaluate, coordinate resolution
   ├─ Task ready for QA → Notify QA
   ├─ QA passed → Notify Documenter
   ├─ QA failed → Notify Dev with feedback
   ├─ Question asked with @pm mention → Answer or escalate
   └─ Escalation from Main PM → Handle priority change

2. PERIODIC SCAN (every 30 min):
   ├─ Check all cell tasks:
   │   ├─ Any stuck? (no progress in 2+ hours)
   │   ├─ Any blocked without resolution path?
   │   └─ Any approaching deadline?
   ├─ Check channel activity:
   │   ├─ Unanswered questions?
   │   └─ Communication issues?
   └─ Take action on findings

3. DAILY SUMMARY (once per day):
   ├─ Compile cell status
   ├─ Report to Main PM
   └─ Flag any concerns
```

```
┌─────────────────────────────────────────────────────────────────────┐
│                     MAIN PM OPERATING LOOP                          │
└─────────────────────────────────────────────────────────────────────┘

1. EVENT TRIGGERS:
   ├─ New initiative from Board → Break down, distribute to cells
   ├─ Escalation from Cell PM → Coordinate resolution
   ├─ Cross-cell dependency → Facilitate communication
   ├─ Cell PM daily report → Aggregate, analyze
   └─ Board request → Handle immediately

2. PERIODIC SCAN (every hour):
   ├─ Check cross-cell dependencies
   ├─ Check overall velocity
   ├─ Identify bottlenecks
   └─ Proactive coordination

3. DAILY REPORT TO BOARD:
   ├─ Overall status
   ├─ Completed work
   ├─ Blockers and risks
   └─ Recommendations
```

#### 8.2 PM Implementation

```python
class PMAgent(BaseAgent):
    """PM agent with event-driven + periodic operating model."""

    async def run(self):
        # Subscribe to relevant events
        event_task = asyncio.create_task(self.handle_events())

        # Periodic scanning
        scan_task = asyncio.create_task(self.periodic_scan())

        # Daily reporting
        report_task = asyncio.create_task(self.daily_report_loop())

        await asyncio.gather(event_task, scan_task, report_task)

    async def handle_events(self):
        """React to events as they occur."""
        async for event in self.event_stream():
            if event.type == "task_blocked":
                await self.handle_blocker(event)
            elif event.type == "task_ready_for_qa":
                await self.route_to_qa(event)
            elif event.type == "question_asked":
                await self.handle_question(event)
            # ... etc

    async def periodic_scan(self):
        """Check for issues periodically."""
        while True:
            await asyncio.sleep(1800)  # 30 minutes

            stuck_tasks = await self.find_stuck_tasks()
            for task in stuck_tasks:
                await self.check_in_with_agent(task)

            unanswered = await self.find_unanswered_questions()
            for question in unanswered:
                await self.answer_or_escalate(question)
```

#### 8.3 PM Spawning Strategy

PMs are "lightweight always-on" - they don't need full Claude Code running constantly:

1. **Event listener** (Python process, not Claude Code) monitors for triggers
2. When event occurs, spawns PM Claude Code instance
3. PM handles event, then terminates
4. For periodic scans, spawns on schedule

This keeps costs down while maintaining responsiveness.

---

### 9. Board Agents Operating Model

The Board (Product Owner, Head of Marketing) operates at strategic level - low frequency, high impact.

#### 9.1 Product Owner

**Role:** Owns product vision, prioritizes backlog, approves major features.

**Triggers:**
```
PRODUCT OWNER SPAWN TRIGGERS:
├─ Main PM escalates prioritization decision
├─ New initiative needs strategic approval
├─ CEO requests product direction input
├─ Weekly strategy session (scheduled: Monday 9am)
└─ Quarterly planning (scheduled)
```

**Operating Loop:**
```python
class ProductOwnerAgent:
    async def handle_trigger(self, trigger: Trigger):
        if trigger.type == "prioritization_escalation":
            # Review competing priorities
            # Make decision with rationale
            # Communicate back to Main PM
            await self.decide_priority(trigger.context)

        elif trigger.type == "initiative_approval":
            # Review initiative proposal
            # Assess alignment with vision
            # Approve, reject, or request changes
            await self.review_initiative(trigger.context)

        elif trigger.type == "weekly_strategy":
            # Review week's progress
            # Adjust priorities if needed
            # Set focus for coming week
            await self.weekly_review()
```

#### 9.2 Head of Marketing

**Role:** External communication, release announcements, brand consistency.

**Triggers:**
```
HEAD OF MARKETING SPAWN TRIGGERS:
├─ Documentation ready for public release
├─ Release being prepared (needs announcement)
├─ External communication needed
├─ Brand/messaging review requested
├─ Weekly sync with Product Owner (scheduled)
└─ Major milestone reached
```

**Operating Loop:**
```python
class HeadOfMarketingAgent:
    async def handle_trigger(self, trigger: Trigger):
        if trigger.type == "docs_ready_for_release":
            # Review for public-facing quality
            # Check brand consistency
            # Approve or request revisions
            await self.review_public_docs(trigger.context)

        elif trigger.type == "release_preparation":
            # Draft release announcement
            # Coordinate with PO on messaging
            # Prepare changelog summary
            await self.prepare_release(trigger.context)

        elif trigger.type == "weekly_sync":
            # Sync with PO on upcoming releases
            # Plan external communications
            await self.weekly_sync()
```

#### 9.3 Board Meeting Pattern

When strategic decisions require both Board members:

```
BOARD MEETING (Both PO + HoM):
├─ Trigger: CEO calls meeting OR quarterly planning
├─ Setup: Both agents spawned into shared session
├─ Process:
│   ├─ Review agenda (provided by Main PM or CEO)
│   ├─ Each provides perspective
│   ├─ Deliberate on decisions
│   └─ Document decisions in #board-private
└─ Output: Decisions communicated to Main PM for execution
```

---

### 10. File Structure

```
roboco/
├── runtime/                      # NEW - Agent Runtime
│   ├── __init__.py
│   ├── orchestrator.py           # Claude Code instance management
│   ├── bootstrap.py              # System initialization
│   ├── events.py                 # Workflow event handlers
│   └── health.py                 # Health monitoring
│
├── mcp/                          # NEW - MCP Servers
│   ├── __init__.py
│   ├── task_server.py            # Task API MCP bridge
│   ├── message_server.py         # Message API MCP bridge
│   ├── notify_server.py          # Notification API MCP bridge
│   └── journal_server.py         # Journal API MCP bridge
│
├── enforcement/                  # NEW - Rule Enforcement
│   ├── __init__.py
│   ├── channel_access.py         # Channel read/write rules
│   ├── notification_perms.py     # Who can notify whom
│   ├── task_lifecycle.py         # State machine enforcement
│   ├── task_ownership.py         # Who can modify tasks
│   ├── message_validation.py     # Message requirements
│   ├── session_boundaries.py     # Session limits
│   └── handoff_requirements.py   # Completion requirements
│
├── api/
│   ├── routes/
│   │   ├── orchestrator.py       # NEW - Orchestrator management API
│   │   └── ...existing routes... # Updated with enforcement
│
└── ...existing structure...
```

---

### 11. Database: Project Model

Projects need to be tracked in the database to support multi-project workflows.

#### 11.1 Project Table

```python
# roboco/models/project.py

class Project(Base):
    """A project/repository managed by RoboCo."""

    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Repository
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    local_path: Mapped[str] = mapped_column(String(500), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(100), default="main")

    # Technology
    tech_stack: Mapped[str] = mapped_column(String(50))  # python, typescript, mixed
    primary_cell: Mapped[str] = mapped_column(String(50))  # backend, frontend, uxui

    # Commands (project-specific)
    test_command: Mapped[str | None] = mapped_column(String(500))
    lint_command: Mapped[str | None] = mapped_column(String(500))
    typecheck_command: Mapped[str | None] = mapped_column(String(500))
    build_command: Mapped[str | None] = mapped_column(String(500))

    # Paths
    src_path: Mapped[str] = mapped_column(String(200), default="./src")
    docs_path: Mapped[str] = mapped_column(String(200), default="./docs")
    tasks_path: Mapped[str] = mapped_column(String(200), default="./.tasks")

    # Settings
    pr_required: Mapped[bool] = mapped_column(default=True)
    active: Mapped[bool] = mapped_column(default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    tasks: Mapped[list["Task"]] = relationship(back_populates="project")
```

#### 11.2 Task-Project Relationship

```python
# Update to roboco/models/task.py

class Task(Base):
    # ... existing fields ...

    # Add project relationship
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id"),
        nullable=True  # Nullable for backward compatibility
    )
    project: Mapped["Project"] = relationship(back_populates="tasks")
```

#### 11.3 Alembic Migration

```python
# alembic/versions/002_add_projects.py

def upgrade():
    # Create projects table
    op.create_table(
        'projects',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('slug', sa.String(100), unique=True, nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('repo_url', sa.String(500), nullable=False),
        sa.Column('local_path', sa.String(500), nullable=False),
        sa.Column('default_branch', sa.String(100), default='main'),
        sa.Column('tech_stack', sa.String(50)),
        sa.Column('primary_cell', sa.String(50)),
        sa.Column('test_command', sa.String(500)),
        sa.Column('lint_command', sa.String(500)),
        sa.Column('typecheck_command', sa.String(500)),
        sa.Column('build_command', sa.String(500)),
        sa.Column('src_path', sa.String(200), default='./src'),
        sa.Column('docs_path', sa.String(200), default='./docs'),
        sa.Column('tasks_path', sa.String(200), default='./.tasks'),
        sa.Column('pr_required', sa.Boolean, default=True),
        sa.Column('active', sa.Boolean, default=True),
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, default=sa.func.now()),
    )

    # Add project_id to tasks
    op.add_column(
        'tasks',
        sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        'fk_task_project',
        'tasks', 'projects',
        ['project_id'], ['id']
    )

    # Index for common queries
    op.create_index('ix_projects_slug', 'projects', ['slug'])
    op.create_index('ix_projects_active', 'projects', ['active'])
    op.create_index('ix_tasks_project_id', 'tasks', ['project_id'])


def downgrade():
    op.drop_index('ix_tasks_project_id')
    op.drop_index('ix_projects_active')
    op.drop_index('ix_projects_slug')
    op.drop_constraint('fk_task_project', 'tasks')
    op.drop_column('tasks', 'project_id')
    op.drop_table('projects')
```

#### 11.4 Project API Endpoints

```python
# roboco/api/routes/projects.py

router = APIRouter(prefix="/projects", tags=["Projects"])

@router.get("/")
async def list_projects(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db)
) -> list[ProjectRead]:
    """List all projects. Admin only."""
    pass

@router.get("/{slug}")
async def get_project(
    slug: str,
    db: AsyncSession = Depends(get_db)
) -> ProjectRead:
    """Get project by slug."""
    pass

@router.post("/")
async def create_project(
    project: ProjectCreate,
    db: AsyncSession = Depends(get_db)
) -> ProjectRead:
    """Create a new project. CEO only."""
    pass

@router.put("/{slug}")
async def update_project(
    slug: str,
    project: ProjectUpdate,
    db: AsyncSession = Depends(get_db)
) -> ProjectRead:
    """Update project settings. CEO only."""
    pass
```

---

## Cost Management

18 Claude Code instances running constantly would be prohibitively expensive. We use on-demand spawning and smart model selection.

### Spawning Strategy

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AGENT RESOURCE MANAGEMENT                        │
└─────────────────────────────────────────────────────────────────────┘

ALWAYS RUNNING (minimal, Python processes):
├─ Orchestrator process
├─ Event listener (watches for triggers)
└─ Health monitor

SPAWNED ON DEMAND (Claude Code):
├─ Developers: When task available/assigned
├─ QA: When task ready for review
├─ Documenters: When handoff created
├─ Cell PMs: When event requires attention
├─ Main PM: When cross-cell coordination needed
├─ Auditor: Periodic audit cycles
└─ Board: Strategic sessions (scheduled or CEO-triggered)
```

### Model Selection by Role

| Role | Model | Reasoning |
|------|-------|-----------|
| Developers | Sonnet | Balance of capability and cost |
| QA | Sonnet | Needs to understand code |
| Documenters | Haiku | Writing docs, less complex reasoning |
| Cell PMs | Sonnet | Coordination needs good reasoning |
| Main PM | Sonnet | Cross-cell coordination |
| Auditor | Sonnet | Analysis needs capability |
| Board | Opus | Strategic thinking (rare, important) |

### Cost Tracking

```python
class AgentCostTracker:
    async def record_session(self, agent_id: str, session: SessionMetrics):
        """Track costs per agent."""
        await self.db.insert({
            "agent_id": agent_id,
            "session_id": session.id,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "input_tokens": session.input_tokens,
            "output_tokens": session.output_tokens,
            "estimated_cost_usd": self.calculate_cost(session),
            "task_id": session.task_id,
        })

    async def get_daily_report(self) -> CostReport:
        """CEO dashboard: how much did we spend?"""
        return {
            "total_cost_usd": ...,
            "by_agent": {...},
            "by_task": {...},
            "by_team": {...},
            "efficiency_metrics": {
                "cost_per_task_completed": ...,
                "idle_time_percentage": ...,
            }
        }
```

### Budget Alerts

- Daily budget threshold with alerts
- Per-task cost anomaly detection
- Idle time tracking (are we spawning agents that do nothing?)

---

## Implementation Plan

### Phase 7.1: MCP Servers (Foundation)
1. Create MCP server base class
2. Implement Task MCP server
3. Implement Message MCP server
4. Implement Notification MCP server
5. Implement Journal MCP server
6. Create unified MCP config generator

### Phase 7.2: Enforcement Layer
1. Implement channel access enforcement
2. Implement notification permission enforcement
3. Implement task state machine enforcement
4. Implement task ownership enforcement
5. Implement message validation enforcement
6. Implement session boundary enforcement
7. Implement handoff requirements enforcement
8. Wire enforcement into existing API routes

### Phase 7.3: Agent Orchestrator
1. Create orchestrator core
2. Implement agent spawning
3. Implement health monitoring
4. Implement session resumption
5. Create orchestrator management API
6. Add graceful shutdown handling

### Phase 7.4: Bootstrap & Events
1. Create bootstrap script
2. Implement agent creation
3. Implement channel creation
4. Create workflow event handlers
5. Wire events to notifications

### Phase 7.5: Integration & Testing
1. End-to-end test: spawn single agent
2. End-to-end test: agent claims task
3. End-to-end test: Dev → QA → Documenter handoff
4. End-to-end test: notification permissions
5. End-to-end test: invalid action rejection
6. Load test: all 18 agents running

---

## Dependencies

- Claude Code CLI with `--system-prompt-file` and `--mcp-config` support
- MCP Python SDK for server implementation
- Existing RoboCo API (Phases 1-6)
- PostgreSQL for state persistence
- Redis for coordination (optional)

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Claude Code CLI changes | HIGH | Pin to specific version, abstract CLI interaction |
| MCP server complexity | MEDIUM | Start simple, iterate |
| Performance with 18 agents | MEDIUM | Test incrementally, monitor resources |
| State synchronization | MEDIUM | Single source of truth (PostgreSQL), no caching initially |
| Agent stuck/unresponsive | MEDIUM | Health monitoring, auto-restart |

---

## Success Metrics

1. **All 18 agents spawn successfully**
2. **Valid actions succeed, invalid actions fail with clear errors**
3. **Complete Dev → QA → Documenter workflow executes without manual intervention**
4. **Auditor can observe all channels without being visible**
5. **PM notifications are delivered, Dev notification attempts are rejected**
6. **Session boundaries are respected**
7. **Task completion requires all documentation**

---

---

## Agent Workflow & Internal TODO System

### The Core Concept

When an agent picks up a task, Claude Code generates its own internal TODO list (using TodoWrite) based on:
1. The **blueprint** (who am I, how do I work)
2. The **task requirements** (what needs to be done)
3. The **MCP guidance** (what the system tells me to do next)

The MCP tools don't dictate the TODOs - they **validate that required outputs exist** before allowing state transitions.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AGENT GENERATES ITS OWN TODOs                    │
│                  (Claude Code's TodoWrite tool)                     │
│                                                                     │
│  Based on:                                                          │
│  • Blueprint: "You are a backend dev, you follow clean code..."     │
│  • Task: "Implement rate limiting for auth endpoints"               │
│  • MCP Response: "Plan submitted. You may now start execution."     │
│                                                                     │
│  Agent creates:                                                     │
│  □ Read task requirements                                           │
│  □ Review acceptance criteria                                       │
│  □ Check related code                                               │
│  □ Create Redis client utility                                      │
│  □ Build rate limit decorator                                       │
│  □ Apply to auth endpoints                                          │
│  □ Write tests                                                      │
│  □ Run quality checks                                               │
│  □ Write journal notes                                              │
│  □ Create handoff                                                   │
│  □ Submit for QA                                                    │
└─────────────────────────────────────────────────────────────────────┘
```

### The try/except/finally Mental Model

```python
# Agent's internal execution model

try:
    # ═══════════════════════════════════════════════════════════════
    # HAPPY PATH
    # ═══════════════════════════════════════════════════════════════

    understand()      # Read task, ask questions if unclear
    plan()            # Break down, write plan.md

    for sub_task in plan.sub_tasks:
        execute(sub_task)
        commit()
        update_progress()

    verify()          # Tests, lint, acceptance criteria

except Blocker as b:
    # ═══════════════════════════════════════════════════════════════
    # BLOCKED PATH - External dependency, unclear requirement, etc.
    # ═══════════════════════════════════════════════════════════════

    document_blocker(b)           # What, why, what's needed
    communicate_in_channel(b)     # Make it visible
    call roboco_task_block()      # Update status, PM notified

    # Options:
    # - Wait for resolution
    # - Pick up different task (return to SCAN)
    # - Escalate if urgent

except Unclear as u:
    # ═══════════════════════════════════════════════════════════════
    # CONFUSION PATH - Need clarification before proceeding
    # ═══════════════════════════════════════════════════════════════

    ask_question_in_channel(u)    # "Hey PM, what does X mean?"
    wait_for_answer()             # Don't proceed until clear
    # Resume from where stopped

except TestFailure as f:
    # ═══════════════════════════════════════════════════════════════
    # FIX PATH - Tests/lint/typecheck failed
    # ═══════════════════════════════════════════════════════════════

    analyze_failure(f)
    fix_issue()
    # Loop back to verify()

except QARejection as r:
    # ═══════════════════════════════════════════════════════════════
    # REVISION PATH - QA found issues
    # ═══════════════════════════════════════════════════════════════

    read_qa_feedback()
    update_todos_with_fixes()
    # Loop back to execute() for specific fixes
    # Then re-submit for QA

except Interruption as i:
    # ═══════════════════════════════════════════════════════════════
    # INTERRUPTION PATH - Higher priority, session end, crash
    # ═══════════════════════════════════════════════════════════════

    save_checkpoint()             # Where I am, what's next
    write_journal("Paused at...")
    call roboco_task_pause()      # Status: paused, state saved
    # Task stays assigned to me for resume

finally:
    # ═══════════════════════════════════════════════════════════════
    # ALWAYS HAPPENS - Documentation, state persistence
    # ═══════════════════════════════════════════════════════════════

    write_journal()               # What I did, learned, struggled with
    update_task_record()          # Save state for recovery

    if completed:
        create_handoff()          # For documenter
        submit_for_qa()
```

---

### MCP Tool Responses Guide Agent's Next Steps

MCP tools don't just validate - they **guide** the agent to the next step:

#### roboco_task_claim() Response

```json
{
  "status": "claimed",
  "task": {
    "id": "TASK-042",
    "title": "Implement rate limiting for auth endpoints",
    "description": "...",
    "acceptance_criteria": [
      "Rate limit of 5 requests per minute on /login",
      "Rate limit of 10 requests per minute on /register",
      "Returns 429 with Retry-After header when exceeded",
      "Uses Redis for distributed counting"
    ]
  },
  "context": {
    "related_tasks": ["TASK-038 (Redis setup)"],
    "related_code": ["roboco/api/routes/auth.py"],
    "previous_attempts": []
  },
  "next_step": "UNDERSTAND",
  "guidance": "Read the task record at .tasks/active/TASK-042/. Review all acceptance criteria. If ANYTHING is unclear, ask in #backend-cell before proceeding to PLAN. Do NOT start coding until you fully understand what success looks like."
}
```

#### roboco_task_plan() Response

```json
{
  "status": "plan_accepted",
  "plan_saved_to": ".tasks/active/TASK-042/plan.md",
  "sub_tasks_count": 4,
  "next_step": "EXECUTE",
  "guidance": "Your plan has 4 sub-tasks. Work through them sequentially. Commit after each sub-task with a meaningful message. Call roboco_task_progress() after completing each. If you get blocked, call roboco_task_block() immediately."
}
```

#### roboco_task_submit_qa() Responses

```json
// REJECTED - Missing artifacts
{
  "status": "rejected",
  "missing": [
    "journal entries (required: at least 2, found: 0)",
    "handoff.md (required for documenter)"
  ],
  "guidance": "Cannot submit for QA. Please complete the missing artifacts first. Journal entries should document: what you did, what you learned, any struggles encountered. Handoff should summarize the work for the documenter."
}
```

```json
// ACCEPTED - Ready for QA
{
  "status": "submitted",
  "qa_notified": true,
  "qa_agent": "be-qa",
  "next_step": "WAIT_FOR_QA",
  "guidance": "Task submitted for QA review. BE-QA has been notified. You will receive a notification when review is complete. Return to SCAN for new work, or wait if this is high priority."
}
```

#### roboco_task_block() Response

```json
{
  "status": "blocked",
  "blocker_id": "BLK-001",
  "blocker_recorded": true,
  "pm_notified": true,
  "pm_agent": "be-pm",
  "your_options": [
    {
      "action": "wait",
      "description": "Wait for blocker resolution. You remain assigned to this task."
    },
    {
      "action": "switch",
      "description": "Pick up a different task. Call roboco_task_scan() to find available work."
    },
    {
      "action": "escalate",
      "description": "If urgent (>1 hour blocked), call roboco_task_escalate() for Main PM attention."
    }
  ],
  "guidance": "Blocker documented and PM notified. Your progress is saved - when unblocked, you'll resume from where you left off. You remain the task owner."
}
```

---

### Complete Scenario Catalog

#### SCENARIO 1: Happy Path (Dev)

```
Agent: be-dev-1
Task: TASK-042 "Implement rate limiting"

SCAN
├─ roboco_task_scan() → finds TASK-042 assigned to me
└─ TODO: [□ Claim task]

CLAIM
├─ roboco_task_claim("TASK-042")
├─ Response: task details, acceptance criteria
└─ TODO: [✓ Claim task, □ Read requirements, □ Ask if unclear]

UNDERSTAND
├─ Read .tasks/active/TASK-042/README.md
├─ Read acceptance criteria (4 items)
├─ Check related code (auth.py)
├─ Everything clear
└─ TODO: [✓ Read requirements, □ Create plan]

PLAN
├─ roboco_task_plan(approach="Use Redis sliding window...", sub_tasks=[...])
├─ Response: "Plan accepted. You may now start execution."
└─ TODO: [✓ Create plan, □ Sub-task 1, □ Sub-task 2, □ Sub-task 3, □ Sub-task 4]

EXECUTE
├─ Work on sub-task 1: Create Redis client
├─ git commit -m "feat(rate-limit): add Redis client utility"
├─ roboco_task_progress(completed=["Sub-task 1"], next="Sub-task 2")
├─ Repeat for sub-tasks 2, 3, 4
└─ TODO: [✓ Sub-task 1, ✓ Sub-task 2, ✓ Sub-task 3, ✓ Sub-task 4, □ Verify]

VERIFY
├─ uv run pytest → all pass
├─ uv run ruff check → clean
├─ uv run mypy → no errors
├─ Check acceptance criteria → all met
└─ TODO: [✓ Verify, □ Write journal, □ Create handoff, □ Submit QA]

DOCUMENT
├─ roboco_journal_write(type="task_reflection", content="...")
├─ Create handoff.md with commits, summary, docs needed
└─ TODO: [✓ Write journal, ✓ Create handoff, □ Submit QA]

SUBMIT
├─ roboco_task_submit_qa()
├─ Response: "Submitted. BE-QA notified."
└─ TODO: [✓ Submit QA] → Return to SCAN
```

---

#### SCENARIO 2: Blocked by External Dependency

```
Agent: be-dev-1
Task: TASK-042 "Implement rate limiting"
Blocker: Redis not configured in settings

EXECUTE (mid-task)
├─ Working on sub-task 1: Create Redis client
├─ Discover: No Redis config in settings.py
├─ Can't proceed without knowing host/port
│
├─ BLOCKER DETECTED
│
├─ roboco_task_block(
│     reason="Missing Redis configuration",
│     what_needed="Redis host/port in settings.py",
│     who_can_help="BE-PM or infra team"
│   )
│
├─ Response: "Blocked. PM notified. Options: wait, switch, escalate"
│
├─ roboco_message_send(
│     channel="backend-cell",
│     type="blocker",
│     content="BLOCKED on TASK-042: Need Redis config. Where should host/port come from?"
│   )
│
└─ TODO: [◐ Sub-task 1 (blocked), □ Wait for resolution]

WAIT OR SWITCH
├─ Option A: Wait
│   └─ PM coordinates, infra adds config
│   └─ roboco_notify_ack() when PM sends "unblocked"
│   └─ roboco_task_unblock() → resume from checkpoint
│
└─ Option B: Switch
    └─ roboco_task_scan() → find another task
    └─ TASK-042 remains assigned, status: blocked
    └─ When unblocked, will return to it
```

---

#### SCENARIO 3: QA Rejection

```
Agent: be-dev-1 → be-qa → be-dev-1
Task: TASK-042

DEV SUBMITS
├─ roboco_task_submit_qa()
└─ Status: awaiting_qa

QA REVIEWS
├─ be-qa receives notification
├─ Claims review task
├─ Runs tests → finds edge case failure
├─ roboco_task_qa_fail(
│     issues=[
│       "Rate limit not reset after window expires",
│       "Missing test for concurrent requests"
│     ],
│     severity="medium"
│   )
└─ Status: needs_revision

DEV RECEIVES FEEDBACK
├─ Notification: "Revision needed: TASK-042"
├─ Read QA notes
├─ TODO updated:
│   [□ Fix window reset bug]
│   [□ Add concurrent request test]
│   [□ Re-verify]
│   [□ Re-submit QA]
│
├─ Fix issues
├─ New commits
├─ roboco_task_submit_qa() again
└─ QA re-reviews → passes
```

---

#### SCENARIO 4: Interruption (Higher Priority Task)

```
Agent: be-dev-1
Current: TASK-042 (P2)
Interrupt: TASK-099 (P0 - production issue)

WORKING ON TASK-042
├─ In middle of sub-task 3
├─ PM sends notification: "P0 task needs you NOW"
│
├─ INTERRUPTION
│
├─ roboco_task_pause(
│     checkpoint={
│       "current_subtask": 3,
│       "progress": "Halfway through decorator implementation",
│       "next_steps": ["Finish decorator", "Apply to endpoints"]
│     }
│   )
│
├─ roboco_journal_write(
│     type="interruption",
│     content="Paused for P0 task. Left off at: decorator impl line 45"
│   )
│
└─ Status: paused (stays assigned to me)

HANDLE P0
├─ roboco_task_claim("TASK-099")
├─ Work on production fix
├─ Complete and submit
└─ Status: completed

RESUME ORIGINAL
├─ roboco_task_scan() → shows TASK-042 (mine, paused)
├─ roboco_task_resume("TASK-042")
├─ Response includes checkpoint data
├─ Read journal for context
├─ Continue from sub-task 3
└─ Complete as normal
```

---

#### SCENARIO 5: Session End / Context Limit

```
Agent: be-dev-1
Task: TASK-042
Event: Claude Code session ending (context limit)

AUTOMATIC CHECKPOINT
├─ Orchestrator detects session ending
├─ Sends signal to agent
│
├─ Agent's finally block runs:
│   ├─ roboco_task_pause(checkpoint={...})
│   ├─ roboco_journal_write("Session ending. State saved.")
│   └─ All state persisted to DB + .tasks/
│
└─ Session ends

ORCHESTRATOR RESPAWNS
├─ Orchestrator detects agent stopped
├─ Spawns new Claude Code instance
├─ Initial prompt includes:
│   "You were working on TASK-042. Resume by:
│    1. Reading .tasks/active/TASK-042/
│    2. Restoring context from journal
│    3. Continuing from checkpoint"
│
├─ New session starts
├─ Agent reads context
├─ roboco_task_resume("TASK-042")
└─ Continues from saved state
```

---

#### SCENARIO 6: Unclear Requirements (Ask Before Proceeding)

```
Agent: be-dev-1
Task: TASK-042
Issue: Acceptance criteria says "appropriate rate limit" but no numbers

UNDERSTAND phase
├─ Read task requirements
├─ Acceptance criteria: "Apply appropriate rate limits"
├─ No specific numbers given
│
├─ GATE: Do NOT proceed with assumptions
│
├─ roboco_message_send(
│     channel="backend-cell",
│     type="dialogue",
│     content="Question on TASK-042: AC says 'appropriate rate limits' but no numbers. What should the limits be for /login vs /register? @be-pm"
│   )
│
└─ TODO: [□ Wait for clarification]

PM RESPONDS
├─ BE-PM: "Good catch. Use 5/min for login, 10/min for register"
├─ Agent updates understanding
├─ Adds to task notes
└─ Proceeds to PLAN with clear requirements
```

---

#### SCENARIO 7: Discovered Bug (Unrelated to Task)

```
Agent: be-dev-1
Task: TASK-042 (rate limiting)
Discovery: Found SQL injection in auth.py while reading code

DURING UNDERSTAND/EXECUTE
├─ Reading auth.py for context
├─ Notice: raw SQL string interpolation (security issue!)
├─ This is NOT part of my current task
│
├─ DO NOT FIX DIRECTLY (no work without task)
│
├─ roboco_message_send(
│     channel="backend-cell",
│     type="blocker",  # Security issues are blockers
│     content="SECURITY: Found potential SQL injection in auth.py line 42. Not my current task but needs urgent attention. @be-pm"
│   )
│
├─ Continue with TASK-042 (unless PM says otherwise)
└─ PM creates separate task for security fix
```

---

#### SCENARIO 8: Cross-Cell Dependency

```
Agent: be-dev-1 (Backend)
Task: TASK-042 (rate limiting)
Dependency: Need API spec from frontend team

EXECUTE phase
├─ Need to know exact endpoint paths frontend expects
├─ This info lives with frontend team
│
├─ roboco_task_block(
│     reason="Cross-cell dependency",
│     what_needed="API endpoint spec from frontend",
│     who_can_help="FE-PM or FE-Dev"
│   )
│
├─ roboco_message_send(
│     channel="backend-cell",
│     type="blocker",
│     content="BLOCKED: Need API spec from frontend. @be-pm please coordinate with FE team"
│   )
│
└─ BE-PM coordinates with FE-PM
    └─ FE-PM notifies FE-Dev
    └─ Spec provided
    └─ BE-PM notifies BE-Dev-1
    └─ roboco_task_unblock() → resume
```

---

#### SCENARIO 9: Documenter Needs More Info

```
Agent: be-doc (Documenter)
Task: Document TASK-042
Issue: Handoff is incomplete

GATHER phase
├─ Read handoff.md
├─ Missing: code samples for usage
├─ Can't write good docs without examples
│
├─ roboco_message_send(
│     channel="backend-cell",
│     type="dialogue",
│     content="@be-dev-1 Handoff for TASK-042 is missing usage examples. Can you add code samples showing how to use the rate limiter?"
│   )
│
└─ Wait for dev to update handoff

DEV UPDATES
├─ be-dev-1 adds code samples to handoff.md
├─ Notifies documenter in channel
└─ Documenter proceeds with documentation
```

---

#### SCENARIO 10: QA Can't Reproduce

```
Agent: be-qa
Task: Review TASK-042
Issue: Can't reproduce dev's test setup

TEST phase
├─ Try to run tests
├─ Redis not running locally
├─ Tests fail due to environment
│
├─ roboco_message_send(
│     channel="backend-cell",
│     type="dialogue",
│     content="@be-dev-1 Can't reproduce test environment for TASK-042. How do I run Redis locally? Is there a docker-compose?"
│   )
│
└─ Wait for clarification

DEV CLARIFIES
├─ "Use docker-compose up redis, then run tests"
├─ QA follows instructions
├─ Tests pass
└─ Continue with review
```

---

### Idle & Scan Behavior

When an agent has no work, it follows a specific idle flow.

#### Scan Priority Order

```
roboco_task_scan() returns tasks in priority order:

1. PAUSED TASKS (mine)
   └─ Tasks I paused must be resumed first
   └─ Cannot claim new work while paused tasks exist

2. ASSIGNED TASKS (explicitly given to me)
   └─ Tasks PM assigned to me directly

3. AVAILABLE TASKS (team pool)
   └─ Unassigned tasks matching my role/team
   └─ Can claim if nothing in 1 or 2
```

#### Scan Response Structure

```json
{
  "paused_tasks": [
    {"id": "TASK-040", "title": "...", "status": "paused", "checkpoint": {...}}
  ],
  "assigned_tasks": [
    {"id": "TASK-042", "title": "...", "status": "claimed", "priority": "P1"}
  ],
  "available_tasks": [
    {"id": "TASK-043", "title": "...", "status": "pending", "priority": "P2"}
  ],
  "guidance": "You have a paused task. Resume TASK-040 before claiming new work."
}
```

#### Idle Flow

```
Agent completes task
        │
        ▼
roboco_task_scan()
        │
        ├─── Paused tasks? ──► Resume oldest paused task
        │
        ├─── Assigned tasks? ──► Claim and start
        │
        ├─── Available tasks? ──► Claim and start
        │
        └─── Nothing? ──► Signal idle
                              │
                              ▼
                    roboco_agent_idle()
                              │
                              ├─ Post in channel: "Available for work"
                              ├─ Save state
                              └─ Terminate (WAITING_LONG)
                                        │
                                        ▼
                              Orchestrator respawns when
                              PM assigns new task
```

#### Communication on Idle

When going idle, agent posts in cell channel:
```
"Finished TASK-042. No pending work. Available for new assignments."
```

This is informational - actual assignment comes through task system.

---

### MCP Validation Matrix

What each MCP tool validates before allowing the action:

| Tool | Required Inputs | Validates | Rejects If |
|------|----------------|-----------|------------|
| `roboco_task_claim` | task_id | Task exists, not already claimed | Already claimed, wrong team |
| `roboco_task_plan` | task_id, approach, sub_tasks | Plan has content, task is claimed | No approach, empty sub_tasks |
| `roboco_task_start` | task_id | Plan exists | No plan submitted |
| `roboco_task_progress` | task_id, update | Task is in_progress | Not started yet |
| `roboco_task_block` | task_id, reason, what_needed | Task is in_progress | Already blocked |
| `roboco_task_submit_qa` | task_id | Has: commits, journal, handoff | Missing required artifacts |
| `roboco_task_qa_pass` | task_id | Called by QA role, has QA notes | Not QA, no feedback |
| `roboco_task_complete` | task_id | Has: dev notes, QA notes, docs | Missing documentation |
| `roboco_notify_send` | recipients, content | Sender is PM/Board/Auditor | Dev/QA/Doc trying to notify |
| `roboco_message_send` | channel, content, type | Agent has channel access | No access to channel |

---

## Testing Strategy

Testing 18 Claude Code agents is expensive. We use a layered approach.

### Test Layers

```
┌─────────────────────────────────────────────────────────────────────┐
│                         TESTING PYRAMID                             │
└─────────────────────────────────────────────────────────────────────┘

LAYER 5: Full Integration (Expensive, Rare)
├─ All 18 agents running
├─ Real tasks, real communication
├─ Only on major releases, CEO approval
└─ Cost: $$$$$

LAYER 4: Single-Agent Live (Higher Cost)
├─ ONE real Claude Code agent
├─ Real MCP servers, real API
├─ Verify workflow compliance
└─ Cost: $$

LAYER 3: Simulation Tests (Medium, No LLM)
├─ Mock agents with scripted behavior
├─ Test complete workflows
├─ Verify state transitions
└─ Cost: $

LAYER 2: Integration Tests (Medium)
├─ MCP servers against real API
├─ Orchestrator with mock Claude Code
├─ Event flow without LLM
└─ Cost: $

LAYER 1: Unit Tests (Fast, Cheap)
├─ Enforcement rules in isolation
├─ State machine transitions
├─ MCP tool validation
└─ Cost: ¢
```

### Unit Test Examples

```python
def test_cannot_skip_claim():
    """Task must be claimed before starting."""
    task = Task(status="pending")
    with pytest.raises(TaskLifecycleError):
        task.transition_to("in_progress")

def test_blocked_requires_reason():
    """Blocking a task requires a reason."""
    task = Task(status="in_progress")
    with pytest.raises(ValidationError):
        task.block(reason=None)

def test_qa_cannot_review_own_work():
    """QA cannot review tasks they developed."""
    task = Task(developed_by="be-dev-1")
    with pytest.raises(PermissionDeniedError):
        task.submit_qa(reviewer="be-dev-1")
```

### Simulation Tests

```python
class MockDevAgent:
    """Simulates a dev agent with scripted behavior."""

    async def execute_task(self, task_id: str):
        await self.mcp.task_claim(task_id)
        await self.mcp.task_plan(task_id, approach="Mock approach")
        await self.mcp.task_start(task_id)
        await self.mcp.task_progress(task_id, update="Done")
        await self.mcp.task_submit_qa(task_id)

async def test_dev_qa_handoff_simulation():
    task = await create_task(...)

    dev = MockDevAgent("be-dev-1")
    await dev.execute_task(task.id)

    qa = MockQAAgent("be-qa")
    await qa.review_task(task.id, verdict="pass")

    task = await get_task(task.id)
    assert task.status == "awaiting_documentation"
```

### Live Test Budget

- Per CI run: $5 budget
- Per day: $50 budget
- Alerts if approaching limits

---

## Error Recovery

Beyond simple restarts, we need to handle persistent failures.

### Failure Tracking

```python
class FailureTracker:
    agent_failures: dict[str, list[Failure]]
    task_failures: dict[str, list[Failure]]

    async def record_failure(self, agent_id: str, task_id: str | None, error: str):
        failure = Failure(
            agent_id=agent_id,
            task_id=task_id,
            error=error,
            timestamp=datetime.utcnow()
        )

        self.agent_failures[agent_id].append(failure)
        if task_id:
            self.task_failures[task_id].append(failure)

        await self.evaluate_pattern(agent_id, task_id)
```

### Escalation Ladder

```
┌─────────────────────────────────────────────────────────────────────┐
│                    FAILURE HANDLING MATRIX                          │
└─────────────────────────────────────────────────────────────────────┘

FAILURE PATTERN          │ DETECTION              │ ACTION
─────────────────────────┼────────────────────────┼─────────────────────
1 failure                │ Process exit           │ Auto-restart
                         │                        │
2 failures same task     │ Tracker correlation    │ Reassign task to
(within 30 min)          │                        │ different agent
                         │                        │ Flag for PM review
                         │                        │
3 failures same agent    │ Tracker correlation    │ Pause agent
(within 30 min)          │                        │ Alert PM
                         │                        │
3 failures same task     │ Tracker correlation    │ Mark task BLOCKED
(different agents)       │                        │ with "systemic issue"
                         │                        │ Alert PM and Auditor
                         │                        │
5+ failures any          │ Rate monitoring        │ CIRCUIT BREAKER
(within 10 min)          │                        │ Pause all spawning
                         │                        │ Alert CEO
```

### Circuit Breaker

```python
class CircuitBreaker:
    """Prevents cascading failures by pausing spawning."""

    state: str = "closed"  # closed, open, half-open
    failure_count: int = 0

    async def record_failure(self):
        self.failure_count += 1

        if self.failure_count >= 5 and self.state == "closed":
            await self.trip()

    async def trip(self):
        """Open the circuit - stop all agent spawning."""
        self.state = "open"

        await alert_ceo("Circuit breaker tripped - multiple agent failures")
        await orchestrator.pause_all_spawning()

        # Schedule recovery test
        asyncio.create_task(self.schedule_test())

    async def schedule_test(self):
        """After cooldown, try one agent."""
        await asyncio.sleep(300)  # 5 minute cooldown

        self.state = "half-open"
        success = await orchestrator.test_spawn()

        if success:
            self.state = "closed"
            self.failure_count = 0
            await orchestrator.resume_spawning()
        else:
            self.state = "open"
            await self.schedule_test()
```

### Task Quarantine

```python
async def quarantine_task(task_id: str, reason: str):
    """Remove task from active pool until investigated."""
    task = await get_task(task_id)

    task.status = "quarantined"
    task.quarantine_reason = reason
    task.quarantine_at = datetime.utcnow()

    await save_task(task)

    await notify_pm(f"Task {task_id} quarantined: {reason}")
    await create_auditor_flag(
        severity="concern",
        subject=f"Task {task_id}",
        description=f"Quarantined due to repeated failures: {reason}"
    )
```

---

## Quick Context Restore

Phase 7 builds the Agent Runtime - the layer that brings RoboCo to life. We have the infrastructure (Phases 1-6). Now we need: MCP servers to bridge Claude Code and our APIs, enforcement logic to reject invalid actions, an orchestrator to manage instances, bootstrap to create initial state, and event handlers to drive the workflow.

**Key principles:**
1. **Blueprints guide** - Tell agents who they are and how to behave
2. **APIs enforce** - Block invalid actions, validate required artifacts
3. **Agents think** - Claude Code generates its own TODOs based on task + guidance
4. **MCP guides** - Tool responses tell agent what to do next
5. **try/except/finally** - Every path (happy, blocked, interrupted) has defined handling
6. **State persists** - Checkpoints, journals, and task records survive sessions

---

## Implementation Notes (2025-12-10)

### Files Created

#### MCP Servers (`roboco/mcp/`)
| File | Purpose | Tools |
|------|---------|-------|
| `__init__.py` | Module init | Exports all server factories |
| `task_server.py` | Task lifecycle via MCP | scan, get, claim, plan, start, progress, block, unblock, pause, submit_verification, submit_qa, qa_pass, qa_fail, complete |
| `message_server.py` | Channel messaging via MCP | channel_list, channel_history, message_send, message_get, ask_question, report_blocker |
| `notify_server.py` | Notifications via MCP | notify_list, notify_get, notify_ack, notify_send, escalate, request_approval |
| `journal_server.py` | Personal journaling via MCP | journal_entry, journal_reflect, journal_decision, journal_learning, journal_struggle, journal_search, journal_stats, journal_recent |

#### Enforcement Layer (`roboco/enforcement/`)
| File | Purpose | Key Functions |
|------|---------|---------------|
| `__init__.py` | Module init | Exports all validators |
| `channel_access.py` | Channel read/write rules | `validate_channel_access()`, `ChannelAccessDeniedError` |
| `notification_perms.py` | Notification permissions | `validate_notification_permission()`, `NotificationPermissionError` |
| `task_lifecycle.py` | Task state machine | `validate_task_transition()`, `TaskLifecycleError`, `is_terminal_state()`, `is_waiting_state()` |
| `task_ownership.py` | Task ownership rules | `validate_task_ownership()`, `validate_task_claim()`, `can_review_task()` |

#### Runtime (`roboco/runtime/`)
| File | Purpose | Key Classes |
|------|---------|-------------|
| `__init__.py` | Module init | Exports orchestrator |
| `orchestrator.py` | Agent lifecycle management | `AgentOrchestrator`, `AgentInstance`, `AgentState`, `WaitingRecord` |

#### Bootstrap (`roboco/`)
| File | Purpose |
|------|---------|
| `bootstrap.py` | System initialization (DB, agents, channels, memberships) |

### Dependencies Added
- `mcp>=1.0.0` - Model Context Protocol library

### CLI Commands Added
- `roboco-bootstrap` - Initialize database and start orchestrator

### What's Working
- All 4 MCP servers with full tool implementations
- Enforcement layer validates channel access, notifications, task lifecycle, and ownership
- Orchestrator spawns/stops Claude Code instances with per-agent MCP config
- Bootstrap creates 18 agents, 11 channels, and configures memberships
- Model selection by role (haiku for docs, sonnet for dev/QA/PM, opus for Board)
- WAITING_LONG state with event-based respawn
- Auto-restart on crash (up to 3 times)

### Additional Files Created (Second Implementation Pass)

#### Event System (`roboco/events/`)
| File | Purpose | Key Components |
|------|---------|----------------|
| `__init__.py` | Module init | Exports EventBus, Event, EventType, handlers |
| `bus.py` | Redis pub/sub event bus | `EventBus`, `Event`, `EventType` enum |
| `handlers.py` | Workflow trigger handlers | `handle_task_status_change()`, `handle_session_boundary()`, `handle_handoff_created()`, `handle_qa_result()` |

#### API Routes (`roboco/api/routes/`)
| File | Purpose |
|------|---------|
| `orchestrator.py` | Agent orchestrator management API |

#### Services (`roboco/services/`)
| File | Purpose |
|------|---------|
| `notification.py` | System-generated notification service |

### What's Complete
- All 4 MCP servers with full tool implementations
- Enforcement layer validates channel access, notifications, task lifecycle, and ownership
- Orchestrator spawns/stops Claude Code instances with per-agent MCP config
- Bootstrap creates 18 agents, 11 channels, configures memberships, posts initial messages
- Model selection by role (haiku for docs, sonnet for dev/QA/PM, opus for Board)
- WAITING_LONG state with event-based respawn
- Auto-restart on crash (up to 3 times)
- Event system with Redis pub/sub for workflow triggers
- Orchestrator API routes for management
- Notification permission enforcement in API
- Handoff requirement enforcement for task completion
- Initial channel welcome messages

### What's Pending
- **End-to-end testing** - Requires running infrastructure (PostgreSQL, Redis, Claude Code)

### Usage
```bash
# Initialize DB only
roboco-bootstrap --db-only

# Start full system
roboco-bootstrap

# Start with specific agents
roboco-bootstrap --spawn main-pm be-pm fe-pm

# Skip DB (already initialized)
roboco-bootstrap --skip-db --spawn be-dev-1
```
