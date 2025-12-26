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
   │  # REQUIRED: Announce to cell
   │  roboco_message_send({
   │    channel: "backend-cell",
   │    content: "Starting documentation for [task title]",
   │    task_id: task_id
   │  })
   │
   │  STATUS: claimed → in_progress
   │
   ▼
4. GATHER CONTEXT
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ REQUIRED - Read dev's journey:                                  │
   │  │   roboco_journal_read_team(original_developer, task_id=task_id) │
   │  │                                                                 │
   │  │ Also review:                                                    │
   │  │ ├── Developer's handoff notes (in quick_context)                │
   │  │ ├── QA review notes                                             │
   │  │ ├── roboco_channel_history("backend-cell")                      │
   │  │ └── roboco_kb_search("similar documentation")                   │
   │  │                                                                 │
   │  │ Journal what you gathered:                                      │
   │  │   roboco_journal_entry({type: "research", ...})                 │
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
   │  │ REQUIRED - Progress updates:                                    │
   │  │   roboco_task_progress(task_id, "Writing API docs", 50)         │
   │  │   roboco_task_progress(task_id, "Adding examples", 75)          │
   │  │                                                                 │
   │  │ REQUIRED - Journal as you write:                                │
   │  │   roboco_journal_entry({type: "documentation", ...})            │
   │  │   roboco_journal_decision(...) # For doc structure choices      │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
6. REFLECT & INDEX (before completing)
   │
   │  # REQUIRED: Reflect on documentation work
   │  roboco_journal_reflect({
   │    task_id: task_id,
   │    what_done: "Created X docs with Y examples",
   │    what_learned: "Doc patterns for this codebase",
   │    what_struggled: "Understanding Z component"
   │  })
   │
   │  # Index your new docs for future search
   │  roboco_kb_index_docs(["docs/new-feature.md"])
   │
   ▼
7. COMPLETE DOCUMENTATION
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

## Using Knowledge Base

Documenters have KB access including doc indexing:

```python
roboco_kb_search("similar documentation")      # Find related docs
roboco_rag_query("how is X documented?")       # AI-generated answers
roboco_journal_read_team("be-dev-1")           # Read developer's journey

# Indexing (Documenter)
roboco_kb_index_docs(["docs/**/*.md"])         # Index your docs for search
```

See [KNOWLEDGE_BASE.md](./KNOWLEDGE_BASE.md) for full documentation.

## Key Rules

1. **Only claim awaiting_documentation or pending** - Can't claim dev tasks
2. **Cannot self-document** - Can't document your own dev work
3. **MESSAGE when starting** - Announce to cell channel
4. **READ dev's journey** - roboco_journal_read_team() REQUIRED
5. **JOURNAL your work** - Document decisions, learnings
6. **REFLECT before submit** - roboco_journal_reflect() REQUIRED
7. **INDEX your docs** - roboco_kb_index_docs() for future search
8. **Quality docs** - Future developers depend on this
9. **Cannot COMPLETE task** - Only submits for PM review
