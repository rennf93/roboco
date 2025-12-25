# QA Workflow

## Overview

QA agents (be-qa, fe-qa, ux-qa) verify developer work meets acceptance criteria.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           QA WORKFLOW                                   │
└─────────────────────────────────────────────────────────────────────────┘

1. SCAN FOR WORK
   │
   │  roboco_task_scan(team="backend")
   │
   │  Look for:
   │  └── Tasks in "awaiting_qa" status
   │
   ▼
2. CLAIM TASK
   │
   │  roboco_task_claim(task_id)
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ QA can ONLY claim tasks in "awaiting_qa"                        │
   │  │                                                                 │
   │  │ BEFORE:                                                         │
   │  │   status: awaiting_qa                                           │
   │  │   assigned_to: original_developer                               │
   │  │                                                                 │
   │  │ AFTER:                                                          │
   │  │   status: claimed                                               │
   │  │   assigned_to: qa_agent                                         │
   │  │   (original_developer stored in quick_context)                  │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
3. START REVIEW
   │
   │  roboco_task_start(task_id)
   │
   │  STATUS: claimed → in_progress
   │
   ▼
4. REVIEW WORK
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ Review checklist:                                               │
   │  │ ├── Read developer's handoff notes                              │
   │  │ ├── Check acceptance criteria                                   │
   │  │ ├── Run tests                                                   │
   │  │ ├── Verify functionality                                        │
   │  │ └── Check code quality                                          │
   │  │                                                                 │
   │  │ roboco_task_progress(task_id, "Reviewing X", 50)                │
   │  │ roboco_journal_entry({type: "qa_review", ...})                  │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
5. DECISION
   │
   ├──── PASS ────────────────────────────────────────────────────────┐
   │                                                                  │
   │     roboco_task_qa_pass(task_id, {                               │
   │       notes: "All acceptance criteria met. Tests pass."          │
   │     })                                                           │
   │                                                                  │
   │     STATUS: in_progress → awaiting_documentation                 │
   │     → Documenter takes over                                      │
   │                                                                  │
   └──── FAIL ────────────────────────────────────────────────────────┐
                                                                      │
         roboco_task_qa_fail(task_id, {                               │
           notes: "Issues found",                                     │
           issues: [                                                  │
             "Bug: X doesn't work",                                   │
             "Missing: Y not implemented"                             │
           ]                                                          │
         })                                                           │
                                                                      │
         STATUS: in_progress → needs_revision                         │
         ASSIGNED_TO: back to original_developer                      │
         → Developer fixes and resubmits                              │
   │
   ▼
   DONE (for QA)
```

## Self-Review Prevention

```
┌─────────────────────────────────────────────────────────────────────────┐
│ QA CANNOT review tasks they originally developed                        │
│                                                                         │
│ System tracks original_developer in quick_context                       │
│ If QA agent == original_developer → FORBIDDEN                           │
└─────────────────────────────────────────────────────────────────────────┘
```

## Status Transitions (QA perspective)

```
CAN CLAIM:
  awaiting_qa ──claim──► claimed

AFTER CLAIM:
  claimed ──start──► in_progress

DECISIONS:
  in_progress ──qa_pass──► awaiting_documentation
  in_progress ──qa_fail──► needs_revision
```

## Key Rules

1. **Only claim awaiting_qa** - Can't claim pending tasks
2. **Cannot self-review** - Can't QA your own dev work
3. **Thorough notes** - Document what was tested and why
4. **Clear fail reasons** - Developer needs to know what to fix
5. **Cannot COMPLETE** - Only PM completes after docs
