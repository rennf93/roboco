# Developer Workflow

## Overview

Developers (be-dev-1, be-dev-2, fe-dev-1, fe-dev-2, ux-dev) execute implementation tasks.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       DEVELOPER WORKFLOW                                │
└─────────────────────────────────────────────────────────────────────────┘

1. CHECK NOTIFICATIONS
   │
   │  roboco_notify_list()
   │  roboco_notify_ack(notification_id)
   │
   ▼
2. SCAN FOR WORK
   │
   │  roboco_task_scan(team="backend")
   │
   │  Look for:
   │  ├── Tasks in "pending" assigned to ME
   │  ├── Tasks in "pending" unassigned (can claim)
   │  └── My paused tasks (should resume)
   │
   ▼
3. CLAIM TASK
   │
   │  roboco_task_claim(task_id)
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ BEFORE:                                                         │
   │  │   status: pending                                               │
   │  │   assigned_to: null OR my_id (if PM pre-assigned)               │
   │  │                                                                 │
   │  │ AFTER:                                                          │
   │  │   status: claimed                                               │
   │  │   assigned_to: my_id                                            │
   │  │   claimed_at: now                                               │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
4. PLAN
   │
   │  roboco_task_plan(
   │    task_id,
   │    approach: "How I'll solve this",
   │    steps: [
   │      {title: "Step 1", description: "..."},
   │      {title: "Step 2", description: "..."}
   │    ],
   │    risks: ["Potential issue X"],
   │    open_questions: ["Need to clarify Y"]
   │  )
   │
   │  If questions → roboco_message_send() to PM
   │
   ▼
5. START WORK
   │
   │  roboco_task_start(task_id)
   │
   │  STATUS: claimed → in_progress
   │
   ▼
6. EXECUTE (loop)
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ While working:                                                  │
   │  │                                                                 │
   │  │   roboco_task_progress(task_id, "Completed X", 25)              │
   │  │   roboco_task_progress(task_id, "Working on Y", 50)             │
   │  │   roboco_task_progress(task_id, "Almost done", 75)              │
   │  │                                                                 │
   │  │   roboco_journal_entry({                                        │
   │  │     type: "work_log",                                           │
   │  │     content: "What I did and learned"                           │
   │  │   })                                                            │
   │  │                                                                 │
   │  │ If BLOCKED:                                                     │
   │  │   roboco_task_block(task_id, blocker_task_id)  ← blocked by     │
   │  │   OR                                             another task   │
   │  │   roboco_task_escalate(task_id, reason)       ← need PM help    │
   │  │                                                                 │
   │  │ If need to PAUSE:                                               │
   │  │   roboco_task_pause(task_id, reason, checkpoint, remaining)     │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
7. SELF-VERIFY
   │
   │  roboco_task_submit_verification(task_id)
   │
   │  STATUS: in_progress → verifying
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ Self-check:                                                     │
   │  │ ├── Does it meet acceptance criteria?                           │
   │  │ ├── Did I run tests?                                            │
   │  │ ├── Is the code clean?                                          │
   │  │ └── Are my notes complete?                                      │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
8. SUBMIT FOR QA
   │
   │  roboco_task_submit_qa(task_id, {
   │    notes: "What I built and how to test it",
   │    test_instructions: "Steps to verify"
   │  })
   │
   │  STATUS: verifying → awaiting_qa
   │
   ▼
   DONE (for developer) → QA takes over
```

## If QA Fails

```
QA FAILS:
  awaiting_qa ──qa_fail──► needs_revision

DEVELOPER SEES IT:
  roboco_task_scan() shows "needs_revision" task

DEVELOPER CLAIMS AGAIN:
  roboco_task_claim(task_id)
  STATUS: needs_revision → claimed

DEVELOPER FIXES:
  roboco_task_start(task_id)
  ... fix issues ...
  roboco_task_submit_verification(task_id)
  roboco_task_submit_qa(task_id, notes)
```

## Status Transitions (Developer perspective)

```
CAN CLAIM:
  pending ──────────► claimed
  needs_revision ───► claimed

AFTER CLAIM:
  claimed ──start──► in_progress

WHILE WORKING:
  in_progress ──block──► blocked
  in_progress ──pause──► paused
  blocked ─────unblock─► in_progress
  paused ──────resume──► in_progress

SUBMIT:
  in_progress ──verify──► verifying
  verifying ───submit_qa──► awaiting_qa
```

## Key Rules

1. **CLAIM before anything** - Must claim to own the task
2. **PLAN before START** - roboco_task_plan() required before start()
3. **PROGRESS updates** - Keep PM informed with percentage
4. **JOURNAL your work** - Document decisions, learnings, struggles
5. **SELF-VERIFY first** - Check your own work before QA
6. **Cannot COMPLETE** - Only PM completes tasks after full workflow
7. **One task at a time** - Can't claim new task while one is in_progress
