# Task Breakdown: {Initiative Name}

> **Last Updated**: YYYY-MM-DD

---

## Overview

| Cell | Total | Completed | In Progress | Blocked | Pending |
|------|-------|-----------|-------------|---------|---------|
| UX/UI | 0 | 0 | 0 | 0 | 0 |
| Backend | 0 | 0 | 0 | 0 | 0 |
| Frontend | 0 | 0 | 0 | 0 | 0 |
| **Total** | **0** | **0** | **0** | **0** | **0** |

---

## Dependency Graph

```
                    ┌─────────────────┐
                    │   Requirements  │
                    │    (Product)    │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              │              ▼
       ┌──────────┐          │       ┌──────────┐
       │  UX/UI   │          │       │ Backend  │
       │  Design  │          │       │   API    │
       └────┬─────┘          │       └────┬─────┘
            │                │            │
            │                │            │
            └───────┬────────┘            │
                    │                     │
                    ▼                     │
             ┌──────────┐                 │
             │ Frontend │◄────────────────┘
             │   UI     │
             └────┬─────┘
                  │
                  ▼
             ┌──────────┐
             │    QA    │
             └────┬─────┘
                  │
                  ▼
             ┌──────────┐
             │  Launch  │
             └──────────┘
```

---

## UX/UI Cell Tasks

| ID | Title | Type | Priority | Assigned | Status | Blocks |
|----|-------|------|----------|----------|--------|--------|
| TASK-XXX | {title} | design | P{n} | {agent} | {status} | TASK-XXX |

### Task Details

#### TASK-XXX: {Title}
- **Type**: Design
- **Priority**: P{n}
- **Assigned**: {agent}
- **Status**: {status}
- **Blocks**: {what this blocks}
- **Description**: {brief description}
- **Link**: [Task Record](../../active/TASK-XXX-slug/)

---

## Backend Cell Tasks

| ID | Title | Type | Priority | Assigned | Status | Blocks |
|----|-------|------|----------|----------|--------|--------|
| TASK-XXX | {title} | feature | P{n} | {agent} | {status} | TASK-XXX |

### Task Details

#### TASK-XXX: {Title}
- **Type**: Feature
- **Priority**: P{n}
- **Assigned**: {agent}
- **Status**: {status}
- **Blocked By**: {dependencies}
- **Blocks**: {what this blocks}
- **Description**: {brief description}
- **Link**: [Task Record](../../active/TASK-XXX-slug/)

---

## Frontend Cell Tasks

| ID | Title | Type | Priority | Assigned | Status | Blocked By |
|----|-------|------|----------|----------|--------|------------|
| TASK-XXX | {title} | feature | P{n} | {agent} | {status} | TASK-XXX, TASK-XXX |

### Task Details

#### TASK-XXX: {Title}
- **Type**: Feature
- **Priority**: P{n}
- **Assigned**: {agent}
- **Status**: {status}
- **Blocked By**: {UX design task, BE API task}
- **Description**: {brief description}
- **Link**: [Task Record](../../active/TASK-XXX-slug/)

---

## Documentation Tasks

| ID | Title | Cell | Priority | Assigned | Status |
|----|-------|------|----------|----------|--------|
| TASK-XXX | {title} | {cell} | P{n} | {agent} | {status} |

---

## Task Creation Checklist

When breaking down initiative into tasks:

- [ ] UX/UI design tasks identified
- [ ] Backend API tasks identified
- [ ] Frontend implementation tasks identified
- [ ] Documentation tasks identified
- [ ] Dependencies mapped
- [ ] Priorities assigned
- [ ] Tasks created in `.tasks/active/`
- [ ] Tasks linked back to this initiative
- [ ] Cell PMs notified

---

## Notes

{Additional notes on task breakdown, sequencing decisions, etc.}
