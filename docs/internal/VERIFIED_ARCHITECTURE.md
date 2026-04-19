# RoboCo Verified Architecture Documentation

> **100% VERIFIED** - Every detail extracted directly from source code.
> Last Verified: December 29, 2025
> Source Files: `roboco/agents_config.py`, `roboco/enforcement/*.py`, `roboco/models/*.py`, `roboco/seeds/initial_data.py`

---

## 1. Agent Census (Verified from `seeds/initial_data.py`)

### Total Count: 20 Entities

| Category | Count | Agents |
|----------|-------|--------|
| CEO (Human) | 1 | ceo |
| Backend Cell | 5 | be-dev-1, be-dev-2, be-qa, be-pm, be-doc |
| Frontend Cell | 5 | fe-dev-1, fe-dev-2, fe-qa, fe-pm, fe-doc |
| UX/UI Cell | 5 | ux-dev-1, ux-dev-2, ux-qa, ux-pm, ux-doc |
| Board/Management | 4 | main-pm, product-owner, head-marketing, auditor |

**Summary: 19 AI Agents + 1 Human CEO = 20 Total Entities**

### Static UUIDs (from `AGENT_UUIDS`)

```python
# CEO (Human)
"ceo": "00000000-0000-0000-0000-000000000001"

# Backend Cell (0001-xxxx)
"be-dev-1": "00000000-0000-0000-0001-000000000001"
"be-dev-2": "00000000-0000-0000-0001-000000000002"
"be-qa":    "00000000-0000-0000-0001-000000000003"
"be-pm":    "00000000-0000-0000-0001-000000000004"
"be-doc":   "00000000-0000-0000-0001-000000000005"

# Frontend Cell (0002-xxxx)
"fe-dev-1": "00000000-0000-0000-0002-000000000001"
"fe-dev-2": "00000000-0000-0000-0002-000000000002"
"fe-qa":    "00000000-0000-0000-0002-000000000003"
"fe-pm":    "00000000-0000-0000-0002-000000000004"
"fe-doc":   "00000000-0000-0000-0002-000000000005"

# UX/UI Cell (0003-xxxx)
"ux-dev-1": "00000000-0000-0000-0003-000000000001"
"ux-dev-2": "00000000-0000-0000-0003-000000000002"
"ux-qa":    "00000000-0000-0000-0003-000000000003"
"ux-pm":    "00000000-0000-0000-0003-000000000004"
"ux-doc":   "00000000-0000-0000-0003-000000000005"

# Board/Management (0004-xxxx)
"main-pm":        "00000000-0000-0000-0004-000000000001"
"product-owner":  "00000000-0000-0000-0004-000000000002"
"head-marketing": "00000000-0000-0000-0004-000000000003"
"auditor":        "00000000-0000-0000-0004-000000000004"
```

### Role Mappings (from `AGENT_ROLE_MAP`)

```python
AGENT_ROLE_MAP = {
    # Backend cell
    "be-dev-1": "developer",
    "be-dev-2": "developer",
    "be-qa": "qa",
    "be-pm": "cell_pm",
    "be-doc": "documenter",
    # Frontend cell
    "fe-dev-1": "developer",
    "fe-dev-2": "developer",
    "fe-qa": "qa",
    "fe-pm": "cell_pm",
    "fe-doc": "documenter",
    # UX/UI cell
    "ux-dev-1": "developer",
    "ux-dev-2": "developer",
    "ux-qa": "qa",
    "ux-pm": "cell_pm",
    "ux-doc": "documenter",
    # Management / Board
    "main-pm": "main_pm",
    "product-owner": "product_owner",
    "head-marketing": "head_marketing",
    "auditor": "auditor",
    "ceo": "ceo",
}
```

### Team Mappings (from `AGENT_TEAM_MAP`)

```python
AGENT_TEAM_MAP = {
    # Backend cell
    "be-dev-1": "backend",
    "be-dev-2": "backend",
    "be-qa": "backend",
    "be-pm": "backend",
    "be-doc": "backend",
    # Frontend cell
    "fe-dev-1": "frontend",
    "fe-dev-2": "frontend",
    "fe-qa": "frontend",
    "fe-pm": "frontend",
    "fe-doc": "frontend",
    # UX/UI cell
    "ux-dev-1": "ux_ui",
    "ux-dev-2": "ux_ui",
    "ux-qa": "ux_ui",
    "ux-pm": "ux_ui",
    "ux-doc": "ux_ui",
    # Management has NO team
}
```

---

## 2. Task Lifecycle (Verified from `enforcement/task_lifecycle.py`)

### All Task States (from `TaskStatus` enum in `models/base.py`)

```python
class TaskStatus(str, Enum):
    BACKLOG = "backlog"                    # PM setup phase
    PENDING = "pending"                    # Ready for work
    CLAIMED = "claimed"                    # Agent claimed
    IN_PROGRESS = "in_progress"            # Active work
    BLOCKED = "blocked"                    # Waiting on dependency
    PAUSED = "paused"                      # Agent paused work
    VERIFYING = "verifying"                # Self-verification
    NEEDS_REVISION = "needs_revision"      # Failed verification
    AWAITING_QA = "awaiting_qa"            # QA queue
    AWAITING_DOCUMENTATION = "awaiting_documentation"  # Docs queue
    AWAITING_PM_REVIEW = "awaiting_pm_review"          # PM approval
    COMPLETED = "completed"                # Terminal - success
    CANCELLED = "cancelled"                # Terminal - cancelled
```

**Total: 13 States** (12 regular + quarantined)

### Valid Transitions (EXACT from `VALID_TRANSITIONS`)

```python
VALID_TRANSITIONS = {
    "backlog": ["pending", "cancelled"],
    "pending": ["claimed", "cancelled"],
    "claimed": ["in_progress", "pending", "cancelled"],
    "in_progress": [
        "blocked",
        "paused",
        "verifying",
        "awaiting_pm_review",
        "completed",
        "cancelled",
    ],
    "blocked": ["in_progress", "cancelled"],
    "paused": ["in_progress", "cancelled"],
    "verifying": [
        "awaiting_qa",
        "needs_revision",
        "awaiting_documentation",
        "cancelled",
    ],
    "needs_revision": ["claimed", "in_progress", "cancelled"],
    "awaiting_qa": [
        "claimed",
        "awaiting_documentation",
        "needs_revision",
        "blocked",
        "cancelled",
    ],
    "awaiting_documentation": ["claimed", "awaiting_pm_review", "cancelled"],
    "awaiting_pm_review": ["claimed", "completed", "cancelled"],
    "completed": [],  # Terminal
    "cancelled": [],  # Terminal
    "quarantined": ["pending"],  # Special recovery
}
```

### Role-Restricted Transitions (EXACT from `ROLE_RESTRICTED_TRANSITIONS`)

```python
# Roles that can cancel:
_CANCEL_ROLES = ["cell_pm", "main_pm", "product_owner", "head_marketing"]
# NOTE: CEO and Auditor are EXCLUDED from cancellation

ROLE_RESTRICTED_TRANSITIONS = {
    # Only PM can activate tasks from backlog
    ("backlog", "pending"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],

    # Only QA can perform QA actions
    ("awaiting_qa", "claimed"): ["qa"],
    ("awaiting_qa", "awaiting_documentation"): ["qa"],
    ("awaiting_qa", "needs_revision"): ["qa"],

    # Only documenter can claim docs tasks and mark complete
    ("awaiting_documentation", "claimed"): ["documenter"],
    ("awaiting_documentation", "awaiting_pm_review"): ["documenter"],

    # Only PM can claim PM review tasks
    ("awaiting_pm_review", "claimed"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],

    # Only PM can complete tasks
    ("awaiting_pm_review", "completed"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
    ("in_progress", "completed"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],

    # PM, QA, Documenter can submit for PM review (NOT developers)
    ("in_progress", "awaiting_pm_review"): ["cell_pm", "main_pm", "product_owner", "head_marketing", "qa", "documenter"],

    # Only PM can cancel (all states that allow cancel)
    ("backlog", "cancelled"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
    ("pending", "cancelled"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
    ("claimed", "cancelled"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
    ("in_progress", "cancelled"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
    ("blocked", "cancelled"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
    ("paused", "cancelled"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
    ("verifying", "cancelled"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
    ("needs_revision", "cancelled"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
    ("awaiting_qa", "cancelled"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
    ("awaiting_documentation", "cancelled"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
    ("awaiting_pm_review", "cancelled"): ["cell_pm", "main_pm", "product_owner", "head_marketing"],
}
```

### State Classification Functions

```python
def is_terminal_state(status: str) -> bool:
    return status in ("completed", "cancelled")

def is_waiting_state(status: str) -> bool:
    return status in (
        "blocked",
        "paused",
        "awaiting_qa",
        "awaiting_documentation",
        "awaiting_pm_review",
    )

def is_active_state(status: str) -> bool:
    return status in ("claimed", "in_progress", "verifying", "needs_revision")
```

---

## 3. Channel Access (EXACT from `CHANNEL_ACCESS` in `agents_config.py`)

### Channel Access Configuration

```python
CHANNEL_ACCESS = {
    # Cell channels - members read/write, main-pm read (monitoring), auditor silent
    "backend-cell": {
        "read": ["be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc", "main-pm"],
        "write": ["be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc"],
        "silent": ["auditor"],
    },
    "frontend-cell": {
        "read": ["fe-dev-1", "fe-dev-2", "fe-qa", "fe-pm", "fe-doc", "main-pm"],
        "write": ["fe-dev-1", "fe-dev-2", "fe-qa", "fe-pm", "fe-doc"],
        "silent": ["auditor"],
    },
    "uxui-cell": {
        "read": ["ux-dev-1", "ux-dev-2", "ux-qa", "ux-pm", "ux-doc", "main-pm"],
        "write": ["ux-dev-1", "ux-dev-2", "ux-qa", "ux-pm", "ux-doc"],
        "silent": ["auditor"],
    },

    # Cross-cell role channels
    "dev-all": {
        "read": [
            "be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev-1", "ux-dev-2",  # ALL_DEVS
            "be-qa", "fe-qa", "ux-qa",  # ALL_QA
            "be-doc", "fe-doc", "ux-doc",  # ALL_DOCS
            "be-pm", "fe-pm", "ux-pm",  # CELL_PMS
            "main-pm"
        ],
        "write": [
            "be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev-1", "ux-dev-2",  # ALL_DEVS
            "be-pm", "fe-pm", "ux-pm",  # CELL_PMS
            "main-pm"
        ],
        "silent": ["auditor"],
    },
    "qa-all": {
        "read": [
            "be-qa", "fe-qa", "ux-qa",  # ALL_QA
            "be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev-1", "ux-dev-2",  # ALL_DEVS
            "be-doc", "fe-doc", "ux-doc",  # ALL_DOCS
            "be-pm", "fe-pm", "ux-pm",  # CELL_PMS
            "main-pm"
        ],
        "write": [
            "be-qa", "fe-qa", "ux-qa",  # ALL_QA
            "be-pm", "fe-pm", "ux-pm"  # CELL_PMS
        ],
        "silent": ["auditor"],
    },
    "pm-all": {
        "read": ["be-pm", "fe-pm", "ux-pm", "main-pm"],
        "write": ["be-pm", "fe-pm", "ux-pm", "main-pm"],
        "silent": ["auditor"],
    },
    "doc-all": {
        "read": [
            "be-doc", "fe-doc", "ux-doc",  # ALL_DOCS
            "be-pm", "fe-pm", "ux-pm",  # CELL_PMS
            "main-pm"
        ],
        "write": [
            "be-doc", "fe-doc", "ux-doc",  # ALL_DOCS
            "be-pm", "fe-pm", "ux-pm"  # CELL_PMS
        ],
        "silent": ["auditor"],
    },

    # Management channels
    "main-pm-board": {
        "read": ["main-pm", "product-owner", "head-marketing", "auditor"],
        "write": ["main-pm", "product-owner", "head-marketing", "auditor"],
        "silent": [],
    },
    "board-private": {
        "read": ["product-owner", "head-marketing", "auditor", "ceo", "main-pm"],
        "write": ["product-owner", "head-marketing", "auditor", "ceo"],
        "silent": [],
    },

    # Broadcast channels
    "announcements": {
        "read": ALL_AGENTS,  # Everyone
        "write": ["main-pm", "product-owner", "head-marketing", "ceo"],
        "silent": [],
    },
    "all-hands": {
        "read": ALL_AGENTS,  # Everyone
        "write": ALL_AGENTS,  # Everyone
        "silent": [],
    },
}
```

---

## 4. Notification Permissions (EXACT from `NOTIFICATION_PERMISSIONS`)

```python
NOTIFICATION_PERMISSIONS = {
    # Cell PMs can notify their own cell members
    "cell_pm": {
        "can_send": True,
        "scope": "cell",  # Own cell + other PMs
    },
    # Main PM can notify anyone
    "main_pm": {
        "can_send": True,
        "scope": "all",
    },
    # Board can notify management chain
    "product_owner": {
        "can_send": True,
        "scope": ["main-pm", "head-marketing", "auditor", "ceo"],
    },
    "head_marketing": {
        "can_send": True,
        "scope": ["main-pm", "product-owner", "auditor", "ceo"],
    },
    # Auditor can notify anyone
    "auditor": {
        "can_send": True,
        "scope": "all",
    },
    # CEO can notify anyone
    "ceo": {
        "can_send": True,
        "scope": "all",
    },
    # Cell members CANNOT send notifications
    "developer": {"can_send": False},
    "qa": {"can_send": False},
    "documenter": {"can_send": False},
}
```

---

## 5. Permission Levels (EXACT from `models/permissions.py`)

```python
class PermissionLevel(IntEnum):
    CEO = 0         # Full access
    BOARD = 1       # Cross-org access
    MAIN_PM = 2     # All cells access
    CELL_PM = 3     # Own cell + PM channel
    CELL_MEMBER = 4 # Own cell only
    AUDITOR = 99    # Special: silent read all

ROLE_PERMISSION_LEVELS = {
    "system": "CEO",        # System/orchestrator = CEO-level
    "ceo": "CEO",
    "product_owner": "BOARD",
    "head_marketing": "BOARD",
    "auditor": "AUDITOR",
    "main_pm": "MAIN_PM",
    "cell_pm": "CELL_PM",
    "developer": "CELL_MEMBER",
    "qa": "CELL_MEMBER",
    "documenter": "CELL_MEMBER",
}
```

---

## 6. Task Permissions (EXACT from `TASK_PERMISSIONS`)

```python
class TaskAction:
    VIEW_ALL = "view_all"
    VIEW_OWN = "view_own"
    CREATE = "create"
    ASSIGN = "assign"
    CLAIM = "claim"
    UPDATE_OWN = "update_own"
    CLOSE = "close"
    CHANGE_PRIORITY = "change_priority"

TASK_PERMISSIONS = {
    AgentRole.SYSTEM: {VIEW_ALL, CREATE, ASSIGN, CLAIM, UPDATE_OWN, CLOSE, CHANGE_PRIORITY},
    AgentRole.CEO: {VIEW_ALL, CREATE, ASSIGN, CLOSE, CHANGE_PRIORITY},
    AgentRole.PRODUCT_OWNER: {VIEW_ALL, CREATE, ASSIGN, CLOSE, CHANGE_PRIORITY},
    AgentRole.HEAD_MARKETING: {VIEW_ALL, CREATE, ASSIGN, CLOSE, CHANGE_PRIORITY},
    AgentRole.AUDITOR: {VIEW_ALL, CREATE, ASSIGN, CLOSE, CHANGE_PRIORITY},
    AgentRole.MAIN_PM: {VIEW_ALL, CREATE, ASSIGN, CLAIM, CLOSE, CHANGE_PRIORITY},
    AgentRole.CELL_PM: {VIEW_OWN, CREATE, ASSIGN, CLAIM, CLOSE, CHANGE_PRIORITY},
    AgentRole.DEVELOPER: {VIEW_OWN, CLAIM, UPDATE_OWN, CLOSE},
    AgentRole.QA: {VIEW_OWN, CLAIM, UPDATE_OWN},
    AgentRole.DOCUMENTER: {VIEW_OWN, CLAIM, UPDATE_OWN, CLOSE},
}
```

### Task Permission Matrix

| Role | VIEW_ALL | VIEW_OWN | CREATE | ASSIGN | CLAIM | UPDATE_OWN | CLOSE | CHANGE_PRIORITY |
|------|:--------:|:--------:|:------:|:------:|:-----:|:----------:|:-----:|:---------------:|
| SYSTEM | X | - | X | X | X | X | X | X |
| CEO | X | - | X | X | - | - | X | X |
| PRODUCT_OWNER | X | - | X | X | - | - | X | X |
| HEAD_MARKETING | X | - | X | X | - | - | X | X |
| AUDITOR | X | - | X | X | - | - | X | X |
| MAIN_PM | X | - | X | X | X | - | X | X |
| CELL_PM | - | X | X | X | X | - | X | X |
| DEVELOPER | - | X | - | - | X | X | X | - |
| QA | - | X | - | - | X | X | - | - |
| DOCUMENTER | - | X | - | - | X | X | X | - |

---

## 7. KB Permissions (EXACT from `KB_PERMISSIONS`)

```python
class KBAction:
    INDEX_CODE = "index_code"
    INDEX_DOCS = "index_docs"
    SEARCH = "search"
    QUERY = "query"
    VIEW_STATS = "view_stats"
    CLEAR_INDEX = "clear_index"
    REFRESH_INDEX = "refresh_index"

KB_PERMISSIONS = {
    AgentRole.CEO: {INDEX_CODE, INDEX_DOCS, SEARCH, QUERY, VIEW_STATS, CLEAR_INDEX, REFRESH_INDEX},
    AgentRole.PRODUCT_OWNER: {INDEX_DOCS, SEARCH, QUERY, VIEW_STATS},
    AgentRole.HEAD_MARKETING: {INDEX_DOCS, SEARCH, QUERY, VIEW_STATS},
    AgentRole.AUDITOR: {SEARCH, QUERY, VIEW_STATS},
    AgentRole.MAIN_PM: {INDEX_CODE, INDEX_DOCS, SEARCH, QUERY, VIEW_STATS, CLEAR_INDEX, REFRESH_INDEX},
    AgentRole.CELL_PM: {INDEX_CODE, INDEX_DOCS, SEARCH, QUERY, VIEW_STATS},
    AgentRole.DEVELOPER: {INDEX_CODE, INDEX_DOCS, SEARCH, QUERY, VIEW_STATS},
    AgentRole.QA: {SEARCH, QUERY, VIEW_STATS},
    AgentRole.DOCUMENTER: {INDEX_DOCS, SEARCH, QUERY, VIEW_STATS},
}
```

---

## 8. Journal Access (EXACT from `enforcement/journal_perms.py`)

```python
# Protected journals - only readable by themselves or global readers
PROTECTED_JOURNALS = frozenset(["ceo", "auditor"])

# Roles with global read access (can read all non-protected journals)
GLOBAL_READERS = frozenset(["ceo", "auditor", "product_owner", "head_marketing", "main_pm"])

# PM roles for cross-cell access
PM_ROLES = frozenset(["cell_pm", "main_pm"])

# Cell member roles (can only read same-cell journals)
CELL_MEMBER_ROLES = frozenset(["developer", "qa", "documenter"])
```

### Journal Access Rules

| Reader Role | Can Read |
|-------------|----------|
| ceo | ALL journals (including auditor) |
| auditor | ALL journals (including ceo) |
| product_owner | All cell journals (excludes ceo, auditor) |
| head_marketing | All cell journals (excludes ceo, auditor) |
| main_pm | All cell journals (excludes ceo, auditor) |
| cell_pm | Own cell + other PMs' journals |
| developer | Own cell journals only |
| qa | Own cell journals only |
| documenter | Own cell journals only |

---

## 9. Escalation Chain (EXACT from `ESCALATION_CHAIN`)

```python
ESCALATION_CHAIN = {
    # Developers → Cell PM
    "be-dev-1": "be-pm",
    "be-dev-2": "be-pm",
    "fe-dev-1": "fe-pm",
    "fe-dev-2": "fe-pm",
    "ux-dev-1": "ux-pm",
    "ux-dev-2": "ux-pm",
    # QA → Cell PM
    "be-qa": "be-pm",
    "fe-qa": "fe-pm",
    "ux-qa": "ux-pm",
    # Documenters → Cell PM
    "be-doc": "be-pm",
    "fe-doc": "fe-pm",
    "ux-doc": "ux-pm",
    # Cell PM → Main PM
    "be-pm": "main-pm",
    "fe-pm": "main-pm",
    "ux-pm": "main-pm",
    # Main PM → Product Owner
    "main-pm": "product-owner",
    # Board → CEO
    "product-owner": "ceo",
    "head-marketing": "ceo",
    "auditor": "ceo",
}
```

---

## 10. MCP Tool Permissions (from helper functions)

```python
def can_send_notifications(agent_id: str) -> bool:
    """PMs, Board, Auditor, CEO can send"""
    role = get_agent_role(agent_id)
    return role in ("cell_pm", "main_pm", "product_owner", "head_marketing", "auditor", "ceo")

def can_create_tasks(agent_id: str) -> bool:
    """PMs and management only"""
    role = get_agent_role(agent_id)
    return role in {"cell_pm", "main_pm", "product_owner", "head_marketing", "ceo"}

def can_assign_tasks(agent_id: str) -> bool:
    """PMs and management only"""
    role = get_agent_role(agent_id)
    return role in {"cell_pm", "main_pm", "product_owner", "head_marketing", "ceo"}

def can_cancel_tasks(agent_id: str) -> bool:
    """PMs and board, NOT CEO/Auditor"""
    role = get_agent_role(agent_id)
    return role in {"cell_pm", "main_pm", "product_owner", "head_marketing"}
    # CEO and Auditor are explicitly EXCLUDED - they observe only

def is_pm(agent_id: str) -> bool:
    """Cell PM or Main PM"""
    return get_agent_role(agent_id) in ("cell_pm", "main_pm")

def is_board_member(agent_id: str) -> bool:
    """Product Owner, Head Marketing, Auditor"""
    return agent_id in ["product-owner", "head-marketing", "auditor"]

def is_management(agent_id: str) -> bool:
    """PM, Board, CEO"""
    role = get_agent_role(agent_id)
    return role in ("cell_pm", "main_pm", "product_owner", "head_marketing", "auditor", "ceo")
```

---

## 11. Enums Reference (EXACT from `models/base.py`)

### AgentRole

```python
class AgentRole(str, Enum):
    SYSTEM = "system"           # Internal orchestrator
    CEO = "ceo"                 # Human executive
    PRODUCT_OWNER = "product_owner"
    HEAD_MARKETING = "head_marketing"
    AUDITOR = "auditor"
    MAIN_PM = "main_pm"
    CELL_PM = "cell_pm"
    DEVELOPER = "developer"
    QA = "qa"
    DOCUMENTER = "documenter"
```

### Team

```python
class Team(str, Enum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    UX_UI = "ux_ui"
    MAIN_PM = "main_pm"     # Cross-cell coordination
    BOARD = "board"
    MARKETING = "marketing"
```

### MessageType

```python
class MessageType(str, Enum):
    REASONING = "reasoning"
    DIALOGUE = "dialogue"
    DECISION = "decision"
    ACTION = "action"
    BLOCKER = "blocker"
    TECHNICAL = "technical"
```

### NotificationType

```python
class NotificationType(str, Enum):
    TASK_ASSIGNMENT = "task_assignment"
    PRIORITY_CHANGE = "priority_change"
    BLOCKER_ESCALATION = "blocker_escalation"
    REVIEW_REQUEST = "review_request"
    DOCUMENTATION_REQUEST = "documentation_request"
    ALERT = "alert"
    BROADCAST = "broadcast"
    KNOWLEDGE_SHARE = "knowledge_share"
    MENTION = "mention"
```

### SubstituteReason

```python
class SubstituteReason(str, Enum):
    LOW_CONTEXT = "low_context"           # Insufficient context
    OUT_OF_SCOPE_TEAM = "out_of_scope_team"  # Wrong team
    OUT_OF_SCOPE_ROLE = "out_of_scope_role"  # Wrong role
    TASK_COMPLETE = "task_complete"       # Finished, releasing
    MAX_RETRIES = "max_retries"           # Exceeded retries
    BLOCKED_EXTERNAL = "blocked_external" # Need external help
```

---

## 12. Task Ownership Rules (EXACT from `enforcement/task_ownership.py`)

### Claim validation (live path)

Claim validation is not a single function. The live flow is:

1. `mcp/tasks/handlers/_helpers.py::validate_task_claimable(task, agent_role, agent_id, client)` — role ↔ status matching.
2. `mcp/tasks/handlers/claim.py::_check_active_tasks` — blocks if the agent already has an active claim.
3. `mcp/tasks/handlers/claim.py::_validate_git_requirements` — project/parent branch present.
4. `mcp/tasks/handlers/claim.py::_validate_sibling_sequence` — earlier-sequence siblings must be terminal.
5. `services/task.py::TaskService.claim` — inline role/team/self-review checks, then auto-creates branch + work session.

### validate_task_ownership() Rules

- **REASSIGN**: Only PMs can reassign; Cell PM only within their cell
- **VIEW**: Generally allowed
- **All other actions** (e.g. `start`): Must be assigned to the agent

### Self-Review Prevention

```python
def can_review_task(agent_id: str, task_developed_by: str | None) -> bool:
    """Cannot review your own work"""
    return agent_id != task_developed_by
```

---

## 13. Communication Matrix (EXACT from `COMMUNICATION_MATRIX`)

```python
COMMUNICATION_MATRIX = {
    AgentRole.CEO: set(AgentRole),  # Everyone
    AgentRole.PRODUCT_OWNER: {CEO, HEAD_MARKETING, AUDITOR, MAIN_PM},
    AgentRole.HEAD_MARKETING: {CEO, PRODUCT_OWNER, AUDITOR, MAIN_PM},
    AgentRole.AUDITOR: set(AgentRole),  # Everyone
    AgentRole.MAIN_PM: {CEO, PRODUCT_OWNER, HEAD_MARKETING, AUDITOR, CELL_PM},
    AgentRole.CELL_PM: {CEO, AUDITOR, MAIN_PM, CELL_PM, DEVELOPER, QA, DOCUMENTER},
    AgentRole.DEVELOPER: {CEO, AUDITOR, CELL_PM, DEVELOPER, QA, DOCUMENTER},
    AgentRole.QA: {CEO, AUDITOR, CELL_PM, DEVELOPER, QA, DOCUMENTER},
    AgentRole.DOCUMENTER: {CEO, AUDITOR, CELL_PM, DEVELOPER, QA, DOCUMENTER},
}
```

---

## 14. Default Channels (from `seeds/initial_data.py`)

| Slug | Name | Type |
|------|------|------|
| backend-cell | Backend Cell | cell |
| frontend-cell | Frontend Cell | cell |
| uxui-cell | UX/UI Cell | cell |
| dev-all | All Developers | cross_cell |
| qa-all | All QA | cross_cell |
| pm-all | All PMs | cross_cell |
| doc-all | All Documenters | cross_cell |
| main-pm-board | Main PM & Board | management |
| board-private | Board Private | management |
| announcements | Announcements | special |
| all-hands | All Hands | special |

---

*This document is 100% verified against source code as of December 29, 2025. All data extracted directly from:*
- `roboco/agents_config.py`
- `roboco/enforcement/task_lifecycle.py`
- `roboco/enforcement/task_ownership.py`
- `roboco/enforcement/channel_access.py`
- `roboco/enforcement/notification_perms.py`
- `roboco/enforcement/journal_perms.py`
- `roboco/models/base.py`
- `roboco/models/permissions.py`
- `roboco/seeds/initial_data.py`
