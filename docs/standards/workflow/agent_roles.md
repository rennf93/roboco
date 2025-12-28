# Agent Roles and Permissions

Comprehensive reference for agent roles, permissions, and organizational structure in the RoboCo system. Derived from actual implementation in the codebase.

**Source Files:**
- Role Definitions: `roboco/models/base.py` (lines 56-78)
- Agent Config: `roboco/agents_config.py`
- Permissions Model: `roboco/models/permissions.py`

---

## Table of Contents

1. [Agent Roles](#agent-roles)
2. [Organizational Structure](#organizational-structure)
3. [Agent Roster](#agent-roster)
4. [Permission Levels](#permission-levels)
5. [Task Permissions](#task-permissions)
6. [Knowledge Base Permissions](#knowledge-base-permissions)
7. [Communication Permissions](#communication-permissions)
8. [Role Capabilities](#role-capabilities)

---

## Agent Roles

### ROLE-001: AgentRole Enum

**Source:** `roboco/models/base.py`

```python
class AgentRole(str, Enum):
    # System (internal orchestrator operations)
    SYSTEM = "system"

    # Executive
    CEO = "ceo"

    # Board
    PRODUCT_OWNER = "product_owner"
    HEAD_MARKETING = "head_marketing"
    AUDITOR = "auditor"

    # Management
    MAIN_PM = "main_pm"
    CELL_PM = "cell_pm"

    # Cell Members
    DEVELOPER = "developer"
    QA = "qa"
    DOCUMENTER = "documenter"
```

### ROLE-002: Role Descriptions

| Role | Description | Count |
|------|-------------|-------|
| `ceo` | Human executive, final authority | 1 |
| `product_owner` | Product strategy and direction | 1 |
| `head_marketing` | Marketing and external comms | 1 |
| `auditor` | Silent observer, quality oversight | 1 |
| `main_pm` | Coordinates all cells | 1 |
| `cell_pm` | Manages a single cell | 3 |
| `developer` | Writes code | 5 |
| `qa` | Reviews and tests | 3 |
| `documenter` | Writes documentation | 3 |
| **Total** | | **19** |

---

## Organizational Structure

### ROLE-010: Hierarchy

```
                              CEO (Renzo - Human)
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
       Product Owner          Head of Marketing          Auditor
       (Board)                    (Board)             (Silent Observer)
              │                      │                      │
              └──────────────────────┼──────────────────────┘
                                     │
                                  Main PM
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
         Backend Cell          Frontend Cell           UX/UI Cell
              │                      │                      │
         ┌────┴────┐            ┌────┴────┐            ┌────┴────┐
        PM  DEV*2  QA          PM  DEV*2  QA          PM  DEV   QA
             DOC                    DOC                    DOC
```

### ROLE-011: Cells

| Cell | PM | Developers | QA | Documenter |
|------|-----|------------|-----|------------|
| Backend | be-pm | be-dev-1, be-dev-2 | be-qa | be-doc |
| Frontend | fe-pm | fe-dev-1, fe-dev-2 | fe-qa | fe-doc |
| UX/UI | ux-pm | ux-dev | ux-qa | ux-doc |

---

## Agent Roster

### ROLE-020: Complete Agent List

**Source:** `roboco/agents_config.py`

| Slug | Role | Cell | Team |
|------|------|------|------|
| `ceo` | ceo | - | executive |
| `product-owner` | product_owner | - | board |
| `head-marketing` | head_marketing | - | board |
| `auditor` | auditor | - | board |
| `main-pm` | main_pm | - | management |
| `be-pm` | cell_pm | backend | management |
| `fe-pm` | cell_pm | frontend | management |
| `ux-pm` | cell_pm | uxui | management |
| `be-dev-1` | developer | backend | developers |
| `be-dev-2` | developer | backend | developers |
| `fe-dev-1` | developer | frontend | developers |
| `fe-dev-2` | developer | frontend | developers |
| `ux-dev-1` | developer | uxui | developers |
| `ux-dev-2` | developer | uxui | developers |
| `be-qa` | qa | backend | qa |
| `fe-qa` | qa | frontend | qa |
| `ux-qa` | qa | uxui | qa |
| `be-doc` | documenter | backend | documentation |
| `fe-doc` | documenter | frontend | documentation |
| `ux-doc` | documenter | uxui | documentation |

---

## Permission Levels

### ROLE-030: Permission Hierarchy

**Source:** `roboco/models/permissions.py`

```python
ROLE_PERMISSION_LEVELS: dict[str, str] = {
    "system": "CEO",         # System/orchestrator - CEO-level access
    "ceo": "CEO",            # Full access
    "product_owner": "BOARD", # Cross-org access
    "head_marketing": "BOARD", # Cross-org access
    "auditor": "AUDITOR",    # Special: silent read all
    "main_pm": "MAIN_PM",    # All cells access
    "cell_pm": "CELL_PM",    # Own cell + PM channel
    "developer": "CELL_MEMBER", # Own cell only
    "qa": "CELL_MEMBER",     # Own cell only
    "documenter": "CELL_MEMBER", # Own cell only
}
```

### ROLE-031: Level Descriptions

| Level | Description | Scope |
|-------|-------------|-------|
| `CEO` | Full access to everything | Organization-wide |
| `BOARD` | Cross-organization access | Cross-cell |
| `AUDITOR` | Silent read access to all | Read-only, all channels |
| `MAIN_PM` | All cells access | All cells |
| `CELL_PM` | Own cell + PM channel | Single cell + PM |
| `CELL_MEMBER` | Own cell only | Single cell |

---

## Task Permissions

### ROLE-040: Task Permission Matrix

**Source:** `roboco/models/permissions.py`

| Role | VIEW_ALL | VIEW_OWN | CREATE | ASSIGN | CLAIM | UPDATE_OWN | CLOSE | CHANGE_PRIORITY |
|------|:--------:|:--------:|:------:|:------:|:-----:|:----------:|:-----:|:---------------:|
| system | X | | X | X | X | X | X | X |
| ceo | X | | X | X | | | X | X |
| product_owner | X | | X | X | | | X | X |
| head_marketing | X | | X | X | | | X | X |
| auditor | X | | X | X | | | X | X |
| main_pm | X | | X | X | X | X | X | X |
| cell_pm | | X | X | X | X | X | X | X |
| developer | | X | | | X | X | X | |
| qa | | X | | | X | X | | |
| documenter | | X | | | X | X | X | |

### ROLE-041: Key Task Capabilities

**Who can CREATE tasks:**
- ceo, product_owner, head_marketing, auditor
- main_pm, cell_pm

**Who can ASSIGN tasks:**
- ceo, product_owner, head_marketing, auditor
- main_pm, cell_pm

**Who can CLAIM tasks:**
- main_pm, cell_pm
- developer, qa, documenter (role-appropriate statuses)

**Who can CLOSE (complete) tasks:**
- ceo, product_owner, head_marketing, auditor
- main_pm, cell_pm
- developer, documenter (their own tasks)

**Who can CANCEL tasks:**
- cell_pm, main_pm, product_owner, head_marketing
- NOT ceo (by design)
- NOT auditor

---

## Knowledge Base Permissions

### ROLE-050: KB Permission Matrix

**Source:** `roboco/models/permissions.py`

| Role | INDEX_CODE | INDEX_DOCS | SEARCH | QUERY | VIEW_STATS | CLEAR | REFRESH |
|------|:----------:|:----------:|:------:|:-----:|:----------:|:-----:|:-------:|
| ceo | X | X | X | X | X | X | X |
| product_owner | | X | X | X | X | | |
| head_marketing | | X | X | X | | | |
| auditor | | | X | X | X | | |
| main_pm | X | X | X | X | X | X | X |
| cell_pm | X | X | X | X | X | | |
| developer | X | X | X | X | | | |
| qa | | | X | X | | | |
| documenter | | X | X | X | | | |

### ROLE-051: KB Capability Summary

**Who can INDEX_CODE:**
- ceo, main_pm, cell_pm, developer

**Who can INDEX_DOCS:**
- ceo, product_owner, head_marketing
- main_pm, cell_pm
- developer, documenter

**Who can SEARCH/QUERY:**
- Everyone

**Who can CLEAR_INDEX/REFRESH:**
- ceo, main_pm only

---

## Communication Permissions

### ROLE-060: Notification Permissions

**Who CAN send notifications:**
- cell_pm
- main_pm
- product_owner
- head_marketing
- auditor
- ceo

**Who CANNOT send notifications:**
- developer
- qa
- documenter

### ROLE-061: Channel Access

**Cell Channels** (e.g., `#backend-cell`):
- Read: Cell members + Main PM
- Write: Cell members
- Silent: Auditor

**Cross-Cell Channels** (e.g., `#dev-all`, `#qa-all`):
- Read/Write: Respective role members + Cell PMs + Main PM
- Silent: Auditor

**Management Channels** (e.g., `#main-pm-board`):
- Read/Write: Board + Main PM
- Silent: Auditor

**Broadcast Channels** (e.g., `#announcements`):
- Read: Everyone
- Write: PMs and Board only

### ROLE-062: Communication Matrix

Each role can communicate with:

| Role | Can Communicate With |
|------|---------------------|
| CEO | Everyone |
| Board Members | CEO, other board, Auditor, Main PM |
| Auditor | Everyone (silent read all channels) |
| Main PM | CEO, Board, Cell PMs |
| Cell PM | CEO, Auditor, Main PM, other Cell PMs, cell members |
| Cell Members | CEO, Auditor, own Cell PM, other cell members |

---

## Role Capabilities

### ROLE-070: Developer Capabilities

```markdown
CAN:
- Claim pending and needs_revision tasks
- Start, pause, resume work
- Submit for verification and QA
- Block tasks (with dependency)
- Search and query knowledge base
- Index code and documentation
- Journal their work

CANNOT:
- Create or assign tasks
- Pass/fail QA
- Complete tasks
- Cancel tasks
- Send notifications
- Clear/refresh KB indexes
```

### ROLE-071: QA Capabilities

```markdown
CAN:
- Claim awaiting_qa tasks
- Pass or fail QA
- Block tasks
- Search and query knowledge base
- Journal their work

CANNOT:
- Claim pending tasks
- Create or assign tasks
- Index content
- Complete documentation
- Complete tasks
- Cancel tasks
- Send notifications
```

### ROLE-072: Documenter Capabilities

```markdown
CAN:
- Claim awaiting_documentation tasks
- Complete documentation
- Index documentation
- Search and query knowledge base
- Journal their work

CANNOT:
- Claim pending tasks
- Create or assign tasks
- Index code
- Pass/fail QA
- Cancel tasks
- Send notifications
```

### ROLE-073: Cell PM Capabilities

```markdown
CAN:
- Create tasks in backlog
- Activate backlog → pending
- Assign tasks to cell members
- Complete awaiting_pm_review tasks
- Cancel any task (in cell)
- Unblock blocked tasks
- Send notifications
- Index code and documentation
- Full KB access (except clear/refresh)

CANNOT:
- Access other cells' tasks (unless Main PM)
- Clear/refresh KB indexes
```

### ROLE-074: Main PM Capabilities

```markdown
CAN:
- Everything Cell PM can do
- Access ALL cells
- Clear and refresh KB indexes
- Coordinate cross-cell work
```

### ROLE-075: Auditor Capabilities

```markdown
CAN:
- View all tasks
- View all channels (silent)
- Search and query knowledge base
- View KB stats
- Create tasks
- Assign tasks

CANNOT:
- Claim tasks
- Update tasks
- Clear KB indexes
- Write to most channels (silent observer)
```

---

## Quick Reference

### Role by Task Action

| Action | Allowed Roles |
|--------|---------------|
| Create task | CEO, Board, PMs |
| Activate task | PMs |
| Assign task | CEO, Board, PMs |
| Claim task | Developer, QA, Documenter, PMs |
| Pass QA | QA only |
| Fail QA | QA only |
| Complete docs | Documenter only |
| Complete task | PMs only |
| Cancel task | PMs, Board (not CEO, not Auditor) |

### Role Hierarchy

```
CEO
 └─ Board (Product Owner, Head Marketing)
     └─ Auditor (silent observer)
         └─ Main PM
             └─ Cell PMs
                 └─ Cell Members (Developer, QA, Documenter)
```

### Escalation Chain

```
Developer/QA/Documenter → Cell PM → Main PM → Product Owner → CEO
```
