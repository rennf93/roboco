# TASK-001: Phase 4 - Agents

## Status
- **State**: completed
- **Priority**: P0
- **Cell**: board

## Dates
- **Created**: 2025-12-09
- **Completed**: 2025-12-09

## Overview
Implement Phase 4 of the RoboCo system per HOMELAB_TEAM_V0.md blueprint (Section 13.6):
- Define all 17 agent types with role-specific workflows
- Implement the task lifecycle for each role
- Create cell deployment infrastructure
- Enable 3 functioning cells + Board

## Acceptance Criteria
- [x] Developer workflow implemented (SCAN → CLAIM → UNDERSTAND → PLAN → EXECUTE → VERIFY → NOTES → CLOSE)
- [x] QA workflow implemented (MONITOR → RECEIVE → UNDERSTAND → TEST → VERDICT → DOCUMENT → RETURN)
- [x] Documenter workflow implemented (MONITOR → RECEIVE → GATHER → SYNTHESIZE → WRITE → REVIEW → PUBLISH)
- [x] Cell PM workflow implemented (MONITOR → TRIAGE → ASSIGN → FACILITATE → ESCALATE → TRACK → REPORT)
- [x] Main PM workflow implemented (OVERSEE → RECEIVE → PRIORITIZE → COORDINATE → DISTRIBUTE → REPORT UP → FACILITATE)
- [x] Board workflows implemented (Product Owner, Head of Marketing, Auditor)
- [x] Cell factory and deployment system
- [x] Organization factory for complete deployment

## What Was Built

### 1. Developer Agent (`roboco/agents/developer.py`)

| Component | Description |
|-----------|-------------|
| `DevTaskPhase` | Enum: SCAN, CLAIM, UNDERSTAND, PLAN, EXECUTE, VERIFY, NOTES, CLOSE, BLOCKED |
| `TaskContext` | Dataclass tracking current task state, subtasks, commits, journal |
| `DeveloperAgent` | Full lifecycle implementation with LLM integration |

Key features:
- Phase-based task execution
- Subtask breakdown and tracking
- Quality checks (ruff, mypy, pytest)
- Journal entries at each phase
- Handoff creation for documenter
- Factory functions for BE/FE/UX developers

### 2. QA Agent (`roboco/agents/qa.py`)

| Component | Description |
|-----------|-------------|
| `QATaskPhase` | Enum: MONITOR, RECEIVE, UNDERSTAND, TEST, VERDICT, DOCUMENT, RETURN |
| `TestCase` | Dataclass for test execution and results |
| `ReviewContext` | Dataclass tracking review state |
| `QAAgent` | Full review lifecycle implementation |

Key features:
- Test case generation from requirements
- Automated test execution
- Clear PASS/FAIL verdicts
- Specific feedback for failures
- QA report generation

### 3. Documenter Agent (`roboco/agents/documenter.py`)

| Component | Description |
|-----------|-------------|
| `DocTaskPhase` | Enum: MONITOR, RECEIVE, GATHER, SYNTHESIZE, WRITE, REVIEW, PUBLISH |
| `DocType` | Enum: API, README, ARCHITECTURE, CHANGELOG, etc. |
| `DocumentSpec` | Dataclass for document specifications |
| `DocumenterAgent` | Full documentation lifecycle |

Key features:
- Material gathering (notes, commits, conversations)
- Synthesis of what was built
- Automatic document type detection
- Self-review before publish
- Factory functions for all cells

### 4. PM Agents (`roboco/agents/pm.py`)

| Component | Description |
|-----------|-------------|
| `CellPMPhase` | Enum: MONITOR, TRIAGE, ASSIGN, FACILITATE, ESCALATE, TRACK, REPORT |
| `MainPMPhase` | Enum: OVERSEE, RECEIVE, PRIORITIZE, COORDINATE, DISTRIBUTE, REPORT_UP, FACILITATE |
| `CellPMAgent` | Cell-level management |
| `MainPMAgent` | Organization-level coordination |

Key features:
- Continuous duty cycles (never complete)
- Task prioritization and assignment
- Blocker facilitation
- Escalation handling
- Status reporting
- Cross-cell coordination (Main PM)

### 5. Board Agents (`roboco/agents/board.py`)

| Component | Description |
|-----------|-------------|
| `ProductOwnerAgent` | Vision, roadmap, requirements, acceptance |
| `HeadMarketingAgent` | Research, strategy, campaigns, analytics |
| `AuditorAgent` | Silent observation, analysis, CEO reporting |

Auditor special powers:
- Read ALL channels silently
- Query all task history
- Access all commits, docs, notes
- Direct line to CEO
- Can notify anyone (sparingly)

### 6. Factory and Deployment (`roboco/agents/factory.py`)

| Component | Description |
|-----------|-------------|
| `Cell` | Complete cell with PM, Devs, QA, Documenter |
| `Board` | Product Owner, Head of Marketing, Auditor |
| `Organization` | Complete 18-agent organization |

Factory functions:
- `create_backend_cell()` - 5 agents
- `create_frontend_cell()` - 5 agents
- `create_ux_cell()` - 4 agents
- `create_board()` - 3 agents
- `create_organization()` - Complete deployment

Utility functions:
- `get_agent_roster()` - List all agents without instantiation
- `print_org_chart()` - Text-based org visualization

## File Structure
```
roboco/agents/
├── __init__.py        # Updated with all exports
├── base.py            # Phase 1 - Base Agent class
├── orchestrator.py    # Phase 1 - Agent orchestration
├── developer.py       # NEW - Developer lifecycle
├── qa.py              # NEW - QA lifecycle
├── documenter.py      # NEW - Documenter lifecycle
├── pm.py              # NEW - Cell PM and Main PM
├── board.py           # NEW - Product Owner, Marketing, Auditor
└── factory.py         # NEW - Cell and Organization factories
```

## Agent Count

| Level | Count | Agents |
|-------|-------|--------|
| Executive | 1 | CEO (Human) |
| Board | 3 | Product Owner, Head of Marketing, Auditor |
| Management | 1 | Main PM |
| Backend Cell | 5 | PM, 2 Devs, QA, Documenter |
| Frontend Cell | 5 | PM, 2 Devs, QA, Documenter |
| UX/UI Cell | 4 | PM, 1 Dev, QA, Documenter |
| **Total** | **19** | 18 AI + 1 Human CEO |

## Workflow Summary

### Developer Lifecycle
```
SCAN → CLAIM → UNDERSTAND → PLAN → EXECUTE → VERIFY → NOTES → CLOSE
  │                           │        │
  └─────── BLOCKED ───────────┴────────┘
```

### QA Lifecycle
```
MONITOR → RECEIVE → UNDERSTAND → TEST → VERDICT → DOCUMENT → RETURN
                                          │
                                    PASS ─┴─ FAIL
```

### Documenter Lifecycle
```
MONITOR → RECEIVE → GATHER → SYNTHESIZE → WRITE → REVIEW → PUBLISH
```

### Cell PM Lifecycle (Continuous)
```
MONITOR → TRIAGE → ASSIGN → FACILITATE → ESCALATE → TRACK → REPORT
    ▲                                                          │
    └──────────────────────────────────────────────────────────┘
```

## Usage Example

```python
from roboco.agents import create_organization

# Create complete organization
org = create_organization()

# Start all agents
await org.start_all()

# Access specific agents
be_dev = org.get_agent_by_slug("be-dev-1")
auditor = org.board.auditor

# Get cell status
backend_agents = org.get_agents_by_team(Team.BACKEND)

# Stop all agents
await org.stop_all()
```

## Next Steps (Phase 5: Management)
Per HOMELAB_TEAM_V0.md section 13.7:
- [ ] Build Kanban interfaces
- [ ] Create Auditor dashboard
- [ ] Create CEO overview
- [ ] Implement metrics collection
- [ ] Build reporting system

## Quick Context Restore
Phase 4 agents complete. All 17 agent types implemented with role-specific workflows. Each agent follows its lifecycle from the blueprint (Section 7). Factory functions create complete cells and the full organization. Agents load system prompts from blueprint files in `agents/blueprints/`. Ready for Phase 5 which adds management UIs and dashboards.
