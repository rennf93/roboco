# Documenter Workflow

## Overview

Documenters (be-doc, fe-doc, ux-doc) create production documentation from developer work.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       DOCUMENTER WORKFLOW                               │
└─────────────────────────────────────────────────────────────────────────┘

1. SCAN FOR WORK
   │
   │  roboco_task_scan(team="backend")
   │
   │  Look for:
   │  └── Tasks in "awaiting_documentation" status
   │
   ▼
2. CLAIM TASK
   │
   │  roboco_task_claim(task_id)
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ Documenter can claim:                                           │
   │  │ ├── "awaiting_documentation" (normal workflow)                  │
   │  │ └── "pending" (direct docs tasks from PM)                       │
   │  │                                                                 │
   │  │ AFTER:                                                          │
   │  │   status: claimed                                               │
   │  │   assigned_to: documenter                                       │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
3. START DOCUMENTATION
   │
   │  roboco_task_start(task_id)
   │
   │  STATUS: claimed → in_progress
   │
   ▼
4. GATHER CONTEXT
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ Read:                                                           │
   │  │ ├── Developer's handoff notes (in quick_context)                │
   │  │ ├── Developer's journal entries                                 │
   │  │ ├── QA review notes                                             │
   │  │ ├── Related commits                                             │
   │  │ └── Code changes                                                │
   │  │                                                                 │
   │  │ roboco_journal_read_team("be-dev-1") → Read dev's journal       │
   │  │ roboco_channel_history("backend-cell") → Related discussion     │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
5. WRITE DOCUMENTATION
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ Create:                                                         │
   │  │ ├── API documentation                                           │
   │  │ ├── Usage examples                                              │
   │  │ ├── Architecture notes                                          │
   │  │ └── Update README if needed                                     │
   │  │                                                                 │
   │  │ roboco_task_progress(task_id, "Writing API docs", 50)           │
   │  │ roboco_task_progress(task_id, "Adding examples", 75)            │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
6. COMPLETE DOCUMENTATION
   │
   │  roboco_task_docs_complete(task_id)
   │
   │  STATUS: in_progress → awaiting_pm_review
   │
   ▼
   DONE (for documenter) → PM reviews and completes
```

## Self-Documentation Prevention

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Documenter CANNOT document tasks they originally developed              │
│                                                                         │
│ System tracks original_developer in quick_context                       │
│ If documenter == original_developer → FORBIDDEN                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Status Transitions (Documenter perspective)

```
CAN CLAIM:
  awaiting_documentation ──claim──► claimed
  pending ─────────────────claim──► claimed  (direct docs tasks)

AFTER CLAIM:
  claimed ──start──► in_progress

COMPLETE:
  in_progress ──docs_complete──► awaiting_pm_review
```

## Key Rules

1. **Only claim awaiting_documentation or pending** - Can't claim dev tasks
2. **Cannot self-document** - Can't document your own dev work
3. **Read developer's journey** - Use journals and handoff notes
4. **Quality docs** - Future developers depend on this
5. **Cannot COMPLETE task** - Only submits for PM review
