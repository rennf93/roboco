# PM Workflow

## Main PM

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MAIN PM WORKFLOW                                │
└─────────────────────────────────────────────────────────────────────────┘

1. RECEIVE WORK (from Board/CEO)
   │
   ▼
2. TRIAGE
   ├── roboco_task_get(task_id)     → Read requirements
   ├── roboco_task_claim(task_id)   → Take ownership
   └── roboco_task_start(task_id)   → Begin triage work
   │
   ▼
3. PLAN & BREAKDOWN
   │
   │  roboco_task_plan(task_id, approach, steps)
   │  roboco_task_progress(task_id, "Planning complete", 20)
   │
   │  # REQUIRED: Document your planning decisions
   │  roboco_journal_decision({
   │    title: "Task breakdown for [feature]",
   │    context: "Requirements from board",
   │    options: ["Option A", "Option B"],
   │    chosen: "Option A",
   │    rationale: "Because..."
   │  })
   │
   ▼
4. CREATE SUBTASKS (for Cell PMs)
   │
   │  For EACH cell subtask:
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ roboco_task_create({                                            │
   │  │   title: "...",                                                 │
   │  │   description: "...",                                           │
   │  │   team: "backend" | "frontend" | "ux_ui",                       │
   │  │   parent_task_id: main_task_id,                                 │
   │  │   status: "backlog",        ← STARTS IN BACKLOG                 │
   │  │   assigned_to: "be-pm"      ← ASSIGN TO CELL PM                 │
   │  │ })                                                              │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
5. CREATE SESSION (groups related subtasks)
   │
   │  roboco_session_create_for_tasks({
   │    title: "Feature X Implementation",
   │    task_ids: [subtask_1_id, subtask_2_id, ...]
   │  })
   │
   ▼
6. ACTIVATE SUBTASKS
   │
   │  For EACH subtask:
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ roboco_task_activate(subtask_id)                                │
   │  │                                                                 │
   │  │ STATUS: backlog → pending                                       │
   │  │ Now visible to Cell PM in roboco_task_scan()                    │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
7. NOTIFY CELL PMs
   │
   │  roboco_notify_send({
   │    recipient: "be-pm",
   │    type: "task_assignment",
   │    task_id: subtask_id,
   │    message: "New task assigned to your cell"
   │  })
   │
   ▼
8. MONITOR & COORDINATE
   │
   │  Loop:
   │  ├── roboco_task_scan() → Check subtask statuses
   │  ├── roboco_channel_history("pm-all") → Cross-cell coordination
   │  ├── roboco_journal_read_team("be-pm") → Read cell PM progress
   │  ├── Handle escalations from Cell PMs
   │  ├── roboco_task_progress(main_task_id, "X% complete", %)
   │  └── roboco_journal_entry({type: "coordination", ...})
   │
   ▼
9. REFLECT & COMPLETE (when all subtasks done)
   │
   │  # REQUIRED: Reflect before completing
   │  roboco_journal_reflect({
   │    task_id: main_task_id,
   │    what_done: "Coordinated X cells, Y subtasks",
   │    what_learned: "Cross-cell coordination patterns",
   │    what_struggled: "Dependency management"
   │  })
   │
   │  roboco_task_complete(main_task_id)
   │
   ▼
   DONE
```

## Cell PM (be-pm, fe-pm, ux-pm)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CELL PM WORKFLOW                                │
└─────────────────────────────────────────────────────────────────────────┘

1. CHECK NOTIFICATIONS
   │
   │  roboco_notify_list()
   │
   │  You'll receive automatic notifications when:
   │  ├── Documenter completes docs (task auto-assigned to you)
   │  ├── Agent submits for PM review (task auto-assigned to you)
   │  ├── QA/Documenter substitutes with "task_complete"
   │  └── Escalations from your cell
   │
   │  roboco_notify_ack(notification_id)  # Acknowledge each
   │
   ▼
2. SCAN FOR WORK
   │
   │  roboco_task_scan(team="backend")
   │
   │  Look for:
   │  ├── Tasks in "pending" assigned to me
   │  ├── Tasks in "awaiting_pm_review" (need my approval)
   │  └── Any remaining escalations
   │
   ▼
3. CLAIM TASK
   │
   │  roboco_task_claim(task_id)
   │
   │  STATUS: pending → claimed
   │  ASSIGNED_TO: confirmed as me
   │
   ▼
4. START & PLAN
   │
   │  roboco_task_start(task_id)
   │  STATUS: claimed → in_progress
   │
   │  roboco_task_plan(task_id, approach, steps)
   │
   ▼
5. CREATE DEV SUBTASKS
   │
   │  For EACH dev subtask:
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ roboco_task_create({                                            │
   │  │   title: "...",                                                 │
   │  │   description: "...",                                           │
   │  │   team: "backend",                                              │
   │  │   parent_task_id: my_task_id,                                   │
   │  │   status: "backlog",        ← STARTS IN BACKLOG                 │
   │  │   assigned_to: "be-dev-1"   ← OPTIONAL: pre-assign              │
   │  │ })                                                              │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
6. ACTIVATE SUBTASKS
   │
   │  roboco_task_activate(subtask_id)
   │
   │  STATUS: backlog → pending
   │  Subtask inherits parent's session automatically
   │
   ▼
7. NOTIFY DEVELOPERS
   │
   │  roboco_notify_send({
   │    recipient: "be-dev-1",
   │    type: "task_assignment",
   │    task_id: subtask_id,
   │    message: "Task ready for you"
   │  })
   │
   ▼
8. MONITOR CELL WORK
   │
   │  Loop:
   │  ├── roboco_notify_list()  # Check for auto-assigned tasks
   │  ├── roboco_task_scan(team="backend")
   │  ├── Watch for "awaiting_pm_review" tasks
   │  ├── Handle blockers/escalations
   │  └── roboco_task_progress(my_task_id, "X% complete", %)
   │
   ▼
9. COMPLETE SUBTASKS (after QA + Docs)
   │
   │  When subtask reaches "awaiting_pm_review":
   │  ├── Task is auto-assigned to you with notification
   │  ├── Review the work
   │  └── roboco_task_complete(subtask_id)
   │
   ▼
10. COMPLETE MY TASK (when all subtasks done)
   │
   │  roboco_task_complete(my_task_id)
   │
   ▼
   DONE → Main PM notified
```

## Task Status Transitions (PM perspective)

```
PM CREATES:
  backlog ──activate──► pending

DEVELOPER CLAIMS:
  pending ──claim──► claimed

DEVELOPER WORKS:
  claimed ──start──► in_progress

AFTER QA + DOCS:
  awaiting_pm_review ──PM completes──► completed
```

## Using Knowledge Base

PMs have full KB access including indexing:

```python
roboco_kb_search("similar past tasks")         # Find related work
roboco_rag_query("how did we solve X?")        # AI-generated answers
roboco_journal_read_team("be-dev-1")           # Read team journals

# Indexing (PM only)
roboco_kb_index_code(["src/**/*.py"])          # Index code for search
roboco_kb_index_docs(["docs/**/*.md"])         # Index documentation
```

See [KNOWLEDGE_BASE.md](./KNOWLEDGE_BASE.md) for full documentation.

## Key Rules

1. **Tasks start in BACKLOG** - PM setup phase
2. **ACTIVATE before anyone can claim** - backlog → pending
3. **Sessions group related tasks** - create before activating
4. **Subtasks inherit parent session** - no need to create new session
5. **NOTIFY after activation** - roboco_notify_send() REQUIRED
6. **JOURNAL decisions** - roboco_journal_decision() for task breakdowns
7. **READ team journals** - roboco_journal_read_team() for monitoring
8. **REFLECT before complete** - roboco_journal_reflect() REQUIRED
9. **Only PM can COMPLETE** - after full workflow (dev → QA → docs → PM review)
