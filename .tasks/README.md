# Task Management System

This directory contains all task records for RoboCo. Every piece of work is tracked here, enabling context persistence across agent sessions and providing a complete audit trail.

## Directory Structure

```
.tasks/
├── README.md                 # This file
├── index.md                  # Master index of all tasks
├── templates/                # Task templates by type
│   ├── feature.md
│   ├── bugfix.md
│   ├── research.md
│   ├── documentation.md
│   └── design.md
├── initiatives/              # Cross-cell initiatives (epics)
│   └── {initiative-name}/
├── active/                   # Currently in-progress tasks
│   └── TASK-XXX-{slug}/
├── completed/                # Archived tasks by month
│   └── YYYY-MM/
└── blocked/                  # Tasks waiting on blockers
    └── TASK-XXX-{slug}/
```

## Task Lifecycle

```
┌──────────┐     ┌──────────┐     ┌─────────────┐     ┌───────────┐
│ Created  │────►│ Assigned │────►│ In Progress │────►│ Verifying │
└──────────┘     └──────────┘     └─────────────┘     └─────┬─────┘
                                        │                   │
                                        ▼                   ▼
                                  ┌──────────┐       ┌─────────────┐
                                  │ Blocked  │       │ Awaiting QA │
                                  └──────────┘       └─────┬───────┘
                                                           │
                                                           ▼
                                                    ┌──────────────┐
                                                    │ Awaiting Doc │
                                                    └──────┬───────┘
                                                           │
                                                           ▼
                                                    ┌───────────┐
                                                    │ Completed │
                                                    └───────────┘
```

## Task States

| State | Meaning | Location |
|-------|---------|----------|
| `pending` | Created but not started | `active/` |
| `claimed` | Agent has taken ownership | `active/` |
| `in_progress` | Active work happening | `active/` |
| `blocked` | Waiting on something | `blocked/` |
| `paused` | Intentionally stopped | `active/` |
| `verifying` | Self-review in progress | `active/` |
| `awaiting_qa` | Ready for QA review | `active/` |
| `needs_revision` | QA requested changes | `active/` |
| `awaiting_documentation` | Ready for docs | `active/` |
| `completed` | Done | `completed/YYYY-MM/` |
| `cancelled` | Abandoned | `completed/YYYY-MM/` |

## Creating a Task

1. Choose appropriate template from `templates/`
2. Create task directory: `.tasks/active/TASK-XXX-{slug}/`
3. Copy template as `README.md`
4. Fill in details
5. Add to `index.md`

## Task Directory Contents

Each task directory contains:

```
TASK-XXX-{slug}/
├── README.md           # Task overview, status, criteria (REQUIRED)
├── requirements.md     # Detailed requirements (if complex)
├── plan.md            # Implementation plan (created by dev)
├── journal.md         # Agent journey notes (created by dev)
├── decisions.md       # Decision log (as needed)
├── blockers.md        # Blocker documentation (if blocked)
├── qa-review.md       # QA findings (created by QA)
├── handoff.md         # Documenter handoff (created by dev)
└── artifacts/         # Supporting files
    ├── code-samples/
    └── diagrams/
```

## Task ID Format

`TASK-{number}-{slug}`

- **number**: Sequential, zero-padded (001, 002, etc.)
- **slug**: Kebab-case description (max 30 chars)

Examples:
- `TASK-042-auth-rate-limiting`
- `TASK-055-user-preferences-modal`
- `TASK-060-dashboard-redesign`

## Priority Levels

| Priority | Meaning | Response Time |
|----------|---------|---------------|
| P0 | Critical | Drop everything |
| P1 | High | Next up |
| P2 | Medium | Normal queue |
| P3 | Low | When available |

## Cells

| Cell | Code | Focus |
|------|------|-------|
| Backend | `BE` | Python, APIs, services |
| Frontend | `FE` | React, TypeScript, UI |
| UX/UI | `UX` | Figma, design system |
| Board | `BD` | Strategy, marketing |

## Conventions

1. **Always update README.md** when status changes
2. **Journal as you work** - future agents depend on it
3. **Link all commits** in the task record
4. **Create handoff.md** before marking awaiting_documentation
5. **Move to completed/** only after all work is done
6. **Never delete** - move to completed with cancelled status if abandoned

## Index Maintenance

The `index.md` file should always reflect current state:
- Update when tasks are created
- Update when status changes
- Update when tasks complete
- Keep statistics current
