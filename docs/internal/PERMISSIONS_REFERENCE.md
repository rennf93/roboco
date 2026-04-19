# RoboCo Permissions Reference

> Complete reference for all permissions, access controls, and enforcement rules.
> Single source of truth: `roboco/agents_config.py`

---

## Table of Contents

1. [Permission Architecture](#1-permission-architecture)
2. [Permission Levels](#2-permission-levels)
3. [Channel Access Matrix](#3-channel-access-matrix)
4. [Task Permissions](#4-task-permissions)
5. [Notification Permissions](#5-notification-permissions)
6. [Journal Access](#6-journal-access)
7. [Knowledge Base Permissions](#7-knowledge-base-permissions)
8. [MCP Tool Permissions](#8-mcp-tool-permissions)
9. [Enforcement Code Reference](#9-enforcement-code-reference)

---

## 1. Permission Architecture

### Two-Layer Model

RoboCo implements a dual-layer permission system:

```
┌─────────────────────────────────────────────────────────┐
│                    Layer 1: MCP Level                   │
│  Controls which MCP tools are visible to agents         │
│  Source: agents_config.py helper functions              │
│  Enforcement: At MCP server registration time           │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                   Layer 2: API Level                    │
│  Fine-grained validation at request time                │
│  Source: roboco/enforcement/*.py modules                │
│  Enforcement: At FastAPI dependency injection           │
└─────────────────────────────────────────────────────────┘
```

### Enforcement Flow

```
Request → Authentication → Permission Check → Enforcement Validation → Service → Database
            (X-Agent-ID)    (deps.py)          (enforcement/*.py)      (*.py)
```

---

## 2. Permission Levels

### Hierarchy

| Level | Name | Description | Roles |
|-------|------|-------------|-------|
| 0 | CEO | Full system access | ceo |
| 1 | BOARD | Cross-organization access | product_owner, head_marketing |
| 2 | MAIN_PM | All cells access | main_pm |
| 3 | CELL_PM | Own cell + PM coordination channel | cell_pm |
| 4 | CELL_MEMBER | Own cell only | developer, qa, documenter |
| 99 | AUDITOR | Special: Silent read access to everything | auditor |

### Level Capabilities

```python
# Level 0 (CEO)
- Read/write all channels
- View all tasks across organization
- Override permission restrictions
- Force complete tasks with cancelled children
- Full knowledge base access

# Level 1 (BOARD)
- Read all channels (can see all)
- Write to management channels
- View all tasks
- Create, assign, close tasks
- No task cancellation (observe only for Auditor)

# Level 2 (MAIN_PM)
- Read/write all cell channels
- View all tasks
- Full task lifecycle control
- Cross-cell coordination
- Full KB access

# Level 3 (CELL_PM)
- Read/write own cell channel
- Read/write PM coordination channels
- View/manage own cell tasks
- Full lifecycle control within cell
- Can notify cell members

# Level 4 (CELL_MEMBER)
- Read/write own cell channel
- Read cross-role channels (dev-all, qa-all, etc.)
- View own cell tasks
- Claim and work on tasks
- Cannot send notifications

# Level 99 (AUDITOR)
- Silent read access to ALL channels
- Can read all journals (protected)
- Can send notifications to anyone
- Cannot cancel tasks (observe only)
- Reports directly to CEO
```

---

## 3. Channel Access Matrix

### Channel Types

| Type | Examples | Purpose |
|------|----------|---------|
| CELL | #backend-cell, #frontend-cell, #uxui-cell | Internal team communication |
| CROSS_CELL | #dev-all, #qa-all, #pm-all, #doc-all | Role-based coordination |
| MANAGEMENT | #main-pm-board, #board-private | Leadership discussions |
| SPECIAL | #announcements, #all-hands | Company-wide communication |

### Complete Access Table

#### Cell Channels

| Channel | Read | Write | Silent |
|---------|------|-------|--------|
| #backend-cell | be-dev-1, be-dev-2, be-qa, be-pm, be-doc, main-pm | be-dev-1, be-dev-2, be-qa, be-pm, be-doc | auditor |
| #frontend-cell | fe-dev-1, fe-dev-2, fe-qa, fe-pm, fe-doc, main-pm | fe-dev-1, fe-dev-2, fe-qa, fe-pm, fe-doc | auditor |
| #uxui-cell | ux-dev-1, ux-qa, ux-pm, ux-doc, main-pm | ux-dev-1, ux-qa, ux-pm, ux-doc | auditor |

#### Cross-Cell Channels

| Channel | Read | Write | Silent |
|---------|------|-------|--------|
| #dev-all | All devs, all QA, all docs, all PMs, main-pm | All devs, all cell-pms, main-pm | auditor |
| #qa-all | All QA, all devs, all docs, all PMs, main-pm | All QA, all cell-pms | auditor |
| #doc-all | All docs, all PMs, main-pm | All docs, all cell-pms | auditor |
| #pm-all | All cell-pms, main-pm | All cell-pms, main-pm | auditor |

#### Management Channels

| Channel | Read | Write | Silent |
|---------|------|-------|--------|
| #main-pm-board | main-pm, product-owner, head-marketing, auditor | main-pm, product-owner, head-marketing, auditor | - |
| #board-private | product-owner, head-marketing, auditor, ceo, main-pm | product-owner, head-marketing, auditor, ceo | - |

#### Special Channels

| Channel | Read | Write | Silent |
|---------|------|-------|--------|
| #announcements | ALL agents | main-pm, product-owner, head-marketing, ceo | - |
| #all-hands | ALL agents | ALL agents | - |

### Privileged Role Overrides

These roles have special access regardless of channel membership:

```python
PRIVILEGED_ROLES = {
    AgentRole.CEO,      # Full access to everything
    AgentRole.AUDITOR,  # Silent read access to everything
    AgentRole.MAIN_PM   # Full access to all cell channels
}
```

---

## 4. Task Permissions

### Task Actions

| Action | Code | Description |
|--------|------|-------------|
| VIEW_ALL | `view_all` | See all tasks in system |
| VIEW_OWN | `view_own` | See only own cell's tasks |
| CREATE | `create` | Create new tasks |
| ASSIGN | `assign` | Assign tasks to agents |
| CLAIM | `claim` | Claim tasks for self |
| UPDATE_OWN | `update_own` | Update own tasks |
| CLOSE | `close` | Close/complete tasks |
| CHANGE_PRIORITY | `change_priority` | Modify task priority |

### Permission Matrix by Role

| Role | VIEW_ALL | VIEW_OWN | CREATE | ASSIGN | CLAIM | UPDATE_OWN | CLOSE | CHANGE_PRIORITY |
|------|:--------:|:--------:|:------:|:------:|:-----:|:----------:|:-----:|:---------------:|
| SYSTEM | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| CEO | ✓ | ✓ | ✓ | ✓ | - | - | ✓ | ✓ |
| PRODUCT_OWNER | ✓ | ✓ | ✓ | ✓ | - | - | ✓ | ✓ |
| HEAD_MARKETING | ✓ | ✓ | ✓ | ✓ | - | - | ✓ | ✓ |
| AUDITOR | ✓ | ✓ | ✓ | ✓ | - | - | ✓ | ✓ |
| MAIN_PM | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| CELL_PM | - | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| DEVELOPER | - | ✓ | - | - | ✓ | ✓ | ✓ | - |
| QA | - | ✓ | - | - | ✓ | ✓ | - | - |
| DOCUMENTER | - | ✓ | - | - | ✓ | ✓ | ✓ | - |

### Role-Based Claiming Rules

```python
# Which statuses each role can claim from

QA_CLAIMABLE_STATUSES = [
    "pending",           # PM directly assigned
    "awaiting_qa",       # Normal workflow
    "claimed"            # Reassignment by PM
]

DOCUMENTER_CLAIMABLE_STATUSES = [
    "pending",              # PM directly assigned
    "awaiting_documentation",  # Normal workflow
    "claimed"               # Reassignment by PM
]

PM_CLAIMABLE_STATUSES = [
    "pending",
    "awaiting_pm_review",
    "claimed"
]

DEVELOPER_CLAIMABLE_STATUSES = [
    "pending",
    "needs_revision",    # After QA rejection
    "claimed"            # Reassignment
]
```

### Role-Based Status Transitions

| Transition | Allowed Roles |
|------------|---------------|
| BACKLOG → PENDING | cell_pm, main_pm, product_owner, head_marketing |
| AWAITING_QA → * | qa only |
| AWAITING_DOCUMENTATION → * | documenter only |
| AWAITING_PM_REVIEW → COMPLETED | cell_pm, main_pm |
| * → CANCELLED | cell_pm, main_pm, product_owner, head_marketing |
| IN_PROGRESS → COMPLETED | cell_pm, main_pm |

**Note:** CEO and Auditor cannot cancel tasks (they observe only)

---

## 5. Notification Permissions

### Who Can Send Notifications

| Role | Can Send | Scope |
|------|:--------:|-------|
| CEO | ✓ | Anyone |
| PRODUCT_OWNER | ✓ | main-pm, head-marketing, auditor, ceo |
| HEAD_MARKETING | ✓ | main-pm, product-owner, auditor, ceo |
| AUDITOR | ✓ | Anyone |
| MAIN_PM | ✓ | Anyone |
| CELL_PM | ✓ | Own cell members + other PMs |
| DEVELOPER | ✗ | Cannot send |
| QA | ✗ | Cannot send |
| DOCUMENTER | ✗ | Cannot send |

### Notification Types

| Type | Description | Typical Sender |
|------|-------------|----------------|
| TASK_ASSIGNMENT | Task assigned to agent | Cell PM |
| PRIORITY_CHANGE | Task priority modified | Any PM |
| BLOCKER_ESCALATION | Task blocked, needs help | Cell PM |
| REVIEW_REQUEST | Task ready for QA | Cell PM |
| DOCUMENTATION_REQUEST | Task ready for docs | Cell PM |
| ALERT | Urgent notification | Any sender |
| BROADCAST | Non-ACK broadcast | Main PM, Board |
| KNOWLEDGE_SHARE | Cross-agent learning | Any PM |
| MENTION | @mention in chat | Automatic |

### Scope Definitions

```python
# "all" - Can notify any agent in the system
# "cell" - Can notify own cell members + other PMs (for coordination)
# [list] - Can only notify agents in the specific list
```

---

## 6. Journal Access

### Access Hierarchy

| Reader Role | Can Read |
|-------------|----------|
| CEO | All journals |
| AUDITOR | All journals |
| PRODUCT_OWNER | All cell journals (not CEO/Auditor) |
| HEAD_MARKETING | All cell journals (not CEO/Auditor) |
| MAIN_PM | All cell journals (not CEO/Auditor) |
| CELL_PM | Own cell + other PMs |
| DEVELOPER | Own cell only |
| QA | Own cell only |
| DOCUMENTER | Own cell only |

### Protected Journals

The following journals are protected (only readable by themselves or global readers):
- CEO journal
- Auditor journal

### Access Scope by Role

```python
JOURNAL_SCOPES = {
    "ceo": "all",           # All journals
    "auditor": "all",       # All journals
    "product_owner": "all_cells",  # All non-protected
    "head_marketing": "all_cells", # All non-protected
    "main_pm": "all_cells",        # All non-protected
    "cell_pm": "cell_plus_pms",    # Own cell + PM journals
    "developer": "cell",           # Own cell only
    "qa": "cell",                  # Own cell only
    "documenter": "cell"           # Own cell only
}
```

---

## 7. Knowledge Base Permissions

### KB Actions

| Action | Code | Description |
|--------|------|-------------|
| INDEX_CODE | `index_code` | Index source code |
| INDEX_DOCS | `index_docs` | Index documentation |
| SEARCH | `search` | Semantic search |
| QUERY | `query` | RAG query |
| VIEW_STATS | `view_stats` | View index statistics |
| CLEAR_INDEX | `clear_index` | Clear an index |
| REFRESH_INDEX | `refresh_index` | Refresh/rebuild index |

### Permission Matrix

| Role | INDEX_CODE | INDEX_DOCS | SEARCH | QUERY | VIEW_STATS | CLEAR_INDEX | REFRESH_INDEX |
|------|:----------:|:----------:|:------:|:-----:|:----------:|:-----------:|:-------------:|
| CEO | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| PRODUCT_OWNER | - | ✓ | ✓ | ✓ | ✓ | - | - |
| HEAD_MARKETING | - | ✓ | ✓ | ✓ | ✓ | - | - |
| AUDITOR | - | - | ✓ | ✓ | ✓ | - | - |
| MAIN_PM | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| CELL_PM | ✓ | ✓ | ✓ | ✓ | ✓ | - | - |
| DEVELOPER | ✓ | ✓ | ✓ | ✓ | ✓ | - | - |
| QA | - | - | ✓ | ✓ | ✓ | - | - |
| DOCUMENTER | - | ✓ | ✓ | ✓ | ✓ | - | - |

---

## 8. MCP Tool Permissions

### Helper Functions

These functions in `agents_config.py` control MCP tool visibility:

```python
def can_create_tasks(agent_id: str) -> bool:
    """Only PMs can create tasks"""
    return get_agent_role(agent_id) in PM_ROLES

def can_assign_tasks(agent_id: str) -> bool:
    """Only PMs can assign tasks"""
    return get_agent_role(agent_id) in PM_ROLES

def can_cancel_tasks(agent_id: str) -> bool:
    """PMs and Board (not CEO/Auditor) can cancel"""
    role = get_agent_role(agent_id)
    return role in ["cell_pm", "main_pm", "product_owner", "head_marketing"]

def can_send_notifications(agent_id: str) -> bool:
    """PMs, Board, Auditor, CEO can send"""
    role = get_agent_role(agent_id)
    return role in ["cell_pm", "main_pm", "product_owner",
                    "head_marketing", "auditor", "ceo"]

def is_pm(agent_id: str) -> bool:
    """Is this agent a PM (cell or main)?"""
    return get_agent_role(agent_id) in ["cell_pm", "main_pm"]

def is_board_member(agent_id: str) -> bool:
    """Is this agent on the board?"""
    return get_agent_role(agent_id) in ["product_owner", "head_marketing", "auditor"]

def is_management(agent_id: str) -> bool:
    """Is this agent in management (PM, Board, or CEO)?"""
    role = get_agent_role(agent_id)
    return role in ["ceo", "product_owner", "head_marketing",
                    "auditor", "main_pm", "cell_pm"]
```

### MCP Tools by Role

| Role | Task Create | Task Assign | Task Cancel | Task Claim | Notify |
|------|:-----------:|:-----------:|:-----------:|:----------:|:------:|
| CEO | ✗ | ✗ | ✗ | ✗ | ✓ |
| PRODUCT_OWNER | ✗ | ✗ | ✓ | ✗ | ✓ |
| HEAD_MARKETING | ✗ | ✗ | ✓ | ✗ | ✓ |
| AUDITOR | ✗ | ✗ | ✗ | ✗ | ✓ |
| MAIN_PM | ✓ | ✓ | ✓ | ✓ | ✓ |
| CELL_PM | ✓ | ✓ | ✓ | ✓ | ✓ |
| DEVELOPER | ✗ | ✗ | ✗ | ✓ | ✗ |
| QA | ✗ | ✗ | ✗ | ✓ | ✗ |
| DOCUMENTER | ✗ | ✗ | ✗ | ✓ | ✗ |

---

## 9. Enforcement Code Reference

### Exception Types

| Exception | Module | HTTP Code | When Raised |
|-----------|--------|-----------|-------------|
| `TaskLifecycleError` | task_lifecycle.py | 409 | Invalid state transition |
| `TaskOwnershipError` | task_ownership.py | 403 | Ownership violation |
| `ChannelAccessDeniedError` | channel_access.py | 403 | Channel access denied |
| `JournalAccessDeniedError` | journal_perms.py | 403 | Journal access denied |
| `NotificationPermissionError` | notification_perms.py | 403 | Cannot send notification |

### Validation Functions

```python
# Task Lifecycle
validate_task_transition(current: str, target: str, agent_role: str | None)
can_agent_transition(current: str, target: str, role: str) -> bool
get_valid_transitions(status: str) -> list[str]
is_terminal_state(status: str) -> bool
is_active_state(status: str) -> bool
is_waiting_state(status: str) -> bool

# Task Ownership
validate_task_ownership(agent_id, task_id, assigned_to, team, action)
can_review_task(agent_id: str, developed_by: str) -> bool
# Claim validation happens in mcp/tasks/handlers/_helpers.validate_task_claimable
# and TaskService.claim — there is no standalone validate_task_claim.

# Channel Access
validate_channel_access(agent_id: str, channel_slug: str, action: str)
get_agent_channels(agent_id: str, action: str) -> list[str]

# Journal Access
validate_journal_access(reader_id: str, owner_id: str)
can_read_journal(reader_id: str, owner_id: str) -> tuple[bool, str]
get_readable_journals(reader_id: str) -> str  # Returns scope

# Notification
validate_notification_permission(sender_id: str, recipients: list[str])
get_notification_scope(agent_id: str) -> str | list[str]
```

### FastAPI Dependencies

```python
# In roboco/api/deps.py

# Channel access validation
require_channel_read(channel_name: str) -> Callable
require_channel_write(channel_name: str) -> Callable

# Notification validation
require_notification_permission() -> Callable

# Task action validation
require_task_action(action: str, task_team: Team | None = None) -> Callable
```

### Usage Example

```python
from roboco.api.deps import require_task_action
from roboco.models.permissions import TaskAction

@router.post("/tasks")
async def create_task(
    request: TaskCreateRequest,
    agent: CurrentAgentContext,
    _: Annotated[None, Depends(require_task_action(TaskAction.CREATE))],
    db: DbSession,
):
    # If we reach here, the agent has CREATE permission
    ...
```

---

## Quick Lookup Tables

### Role → Team Mapping

| Role | Typical Team |
|------|--------------|
| ceo | None |
| product_owner | None (board) |
| head_marketing | None (board) |
| auditor | None (board) |
| main_pm | None (cross-cell) |
| cell_pm | backend/frontend/ux_ui |
| developer | backend/frontend/ux_ui |
| qa | backend/frontend/ux_ui |
| documenter | backend/frontend/ux_ui |

### Agent Slug → Role

| Slug | Role |
|------|------|
| be-dev-1, be-dev-2, fe-dev-1, fe-dev-2, ux-dev-1 | developer |
| be-qa, fe-qa, ux-qa | qa |
| be-doc, fe-doc, ux-doc | documenter |
| be-pm, fe-pm, ux-pm | cell_pm |
| main-pm | main_pm |
| product-owner | product_owner |
| head-marketing | head_marketing |
| auditor | auditor |
| ceo | ceo |

---

*This document reflects the actual implementation in `roboco/agents_config.py` and `roboco/enforcement/` modules as of December 29, 2025.*
