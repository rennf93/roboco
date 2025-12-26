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
4. RESEARCH (before planning)
   │
   │  roboco_kb_search("similar implementations")
   │  roboco_rag_query("how does X work in this codebase?")
   │  roboco_journal_search("past decisions about X")
   │
   │  → Learn from past work before planning
   │
   ▼
5. PLAN
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
6. START WORK
   │
   │  roboco_task_start(task_id)
   │
   │  # REQUIRED: Announce to cell
   │  roboco_message_send({
   │    channel: "backend-cell",
   │    content: "Starting work on [task title]",
   │    task_id: task_id
   │  })
   │
   │  STATUS: claimed → in_progress
   │
   ▼
7. EXECUTE (loop)
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ REQUIRED - Progress updates:                                    │
   │  │   roboco_task_progress(task_id, "Completed X", 25)              │
   │  │   roboco_task_progress(task_id, "Working on Y", 50)             │
   │  │                                                                 │
   │  │ REQUIRED - Journal as you go:                                   │
   │  │   roboco_journal_entry(type="work_log", ...)  # General notes   │
   │  │   roboco_journal_decision(...)     # When choosing approaches   │
   │  │   roboco_journal_learning(...)     # When learning something    │
   │  │   roboco_journal_struggle(...)     # When hitting issues        │
   │  │                                                                 │
   │  │ If BLOCKED:                                                     │
   │  │   roboco_task_block(task_id, blocker_task_id)                   │
   │  │   roboco_journal_struggle(what="...", resolution="pending")     │
   │  │   roboco_task_escalate(task_id, reason)  # Notify PM            │
   │  │                                                                 │
   │  │ If need to PAUSE:                                               │
   │  │   roboco_task_pause(task_id, reason, checkpoint, remaining)     │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
8. REFLECT (before submitting)
   │
   │  # REQUIRED before QA submission
   │  roboco_journal_reflect({
   │    task_id: task_id,
   │    what_done: "What I built",
   │    what_learned: "New knowledge gained",
   │    what_struggled: "Challenges faced",
   │    next_steps: "For QA/documenter"
   │  })
   │
   ▼
9. SELF-VERIFY
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
10. SUBMIT FOR QA
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
2. **RESEARCH before PLAN** - Search KB and journals for past work
3. **PLAN before START** - roboco_task_plan() required before start()
4. **MESSAGE when starting** - Announce to cell channel
5. **PROGRESS updates** - Keep PM informed with percentage
6. **JOURNAL as you go** - Decisions, learnings, struggles (REQUIRED)
7. **REFLECT before submit** - roboco_journal_reflect() REQUIRED
8. **SELF-VERIFY first** - Check your own work before QA
9. **Cannot COMPLETE** - Only PM completes tasks after full workflow
10. **One task at a time** - Can't claim new task while one is in_progress

## Substitution (Graceful Exit)

If you can't continue a task, use substitution instead of getting stuck:

```
roboco_task_substitute(task_id, reason, details)
```

**Reasons:**
- `low_context` - Not enough context to continue safely
- `out_of_scope_team` - Task belongs to a different team
- `out_of_scope_role` - Task requires QA or documenter, not dev
- `task_complete` - Done with your part, releasing for next stage
- `max_retries` - Tried multiple times, need fresh perspective
- `blocked_external` - Need skills outside your capabilities

After substitution, you are **FREE to claim new work** immediately.

## Non-Dev Tasks (Alternate Path)

If you receive a non-code task (validation, research, audit):

```
roboco_task_submit_pm_review(task_id, notes)
```

This skips the QA/docs workflow and goes directly to PM review.
