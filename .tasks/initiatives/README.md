# Initiatives

Initiatives are large, cross-cell efforts that span multiple tasks. They represent features or projects that require coordination between Backend, Frontend, and/or UX/UI cells.

## Directory Structure

```
initiatives/
├── README.md                           # This file
├── {initiative-slug}/
│   ├── README.md                       # Initiative overview
│   ├── requirements.md                 # Detailed requirements from Product Owner
│   ├── tasks.md                        # Task breakdown by cell
│   ├── timeline.md                     # Milestones and deadlines
│   ├── decisions.md                    # Cross-cell decisions
│   └── status.md                       # Current status (updated frequently)
```

## Initiative Lifecycle

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Proposed   │────►│   Planning   │────►│   Active     │────►│  Completed   │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
                            │                    │
                            ▼                    ▼
                     ┌──────────────┐     ┌──────────────┐
                     │   Blocked    │     │   On Hold    │
                     └──────────────┘     └──────────────┘
```

## Creating an Initiative

1. Product Owner or Main PM creates initiative directory
2. Fill in README.md with overview
3. Define requirements in requirements.md
4. Break down tasks by cell in tasks.md
5. Set timeline and milestones in timeline.md
6. Create individual tasks in `.tasks/active/`
7. Link tasks back to initiative

## Initiative Naming

`{descriptive-slug}` - Use kebab-case, max 40 characters

Examples:
- `user-preferences`
- `dashboard-redesign`
- `auth-system-overhaul`
- `mobile-app-v2`
