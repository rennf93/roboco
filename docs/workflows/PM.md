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
   ├── roboco_task_plan(task_id, approach, steps)
   ├── Identify which cells need subtasks
   └── roboco_task_progress(task_id, "Planning complete", 20)
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
   │  ├── Handle escalations from Cell PMs
   │  └── roboco_task_progress(main_task_id, "X% complete", %)
   │
   ▼
9. COMPLETE (when all subtasks done)
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

1. SCAN FOR WORK
   │
   │  roboco_task_scan(team="backend")
   │
   │  Look for:
   │  ├── Tasks in "pending" assigned to me
   │  ├── Tasks in "awaiting_pm_review" (need my approval)
   │  └── Escalations from my cell
   │
   ▼
2. CLAIM TASK
   │
   │  roboco_task_claim(task_id)
   │
   │  STATUS: pending → claimed
   │  ASSIGNED_TO: confirmed as me
   │
   ▼
3. START & PLAN
   │
   │  roboco_task_start(task_id)
   │  STATUS: claimed → in_progress
   │
   │  roboco_task_plan(task_id, approach, steps)
   │
   ▼
4. CREATE DEV SUBTASKS
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
5. ACTIVATE SUBTASKS
   │
   │  roboco_task_activate(subtask_id)
   │
   │  STATUS: backlog → pending
   │  Subtask inherits parent's session automatically
   │
   ▼
6. NOTIFY DEVELOPERS
   │
   │  roboco_notify_send({
   │    recipient: "be-dev-1",
   │    type: "task_assignment",
   │    task_id: subtask_id,
   │    message: "Task ready for you"
   │  })
   │
   ▼
7. MONITOR CELL WORK
   │
   │  Loop:
   │  ├── roboco_task_scan(team="backend")
   │  ├── Watch for "awaiting_pm_review" tasks
   │  ├── Handle blockers/escalations
   │  └── roboco_task_progress(my_task_id, "X% complete", %)
   │
   ▼
8. COMPLETE SUBTASKS (after QA + Docs)
   │
   │  When subtask reaches "awaiting_pm_review":
   │  ├── Review the work
   │  └── roboco_task_complete(subtask_id)
   │
   ▼
9. COMPLETE MY TASK (when all subtasks done)
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

## Key Rules

1. **Tasks start in BACKLOG** - PM setup phase
2. **ACTIVATE before anyone can claim** - backlog → pending
3. **Sessions group related tasks** - create before activating
4. **Subtasks inherit parent session** - no need to create new session
5. **Only PM can COMPLETE** - after full workflow (dev → QA → docs → PM review)
