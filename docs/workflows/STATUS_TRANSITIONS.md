# Task Status Transitions

## Complete Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          FULL TASK LIFECYCLE                                    │
└─────────────────────────────────────────────────────────────────────────────────┘

                              PM CREATES
                                  │
                                  ▼
                            ┌──────────┐
                            │ BACKLOG  │ ◄─── PM setup phase
                            └────┬─────┘      (create session, plan)
                                 │
                            PM ACTIVATES
                                 │
                                 ▼
                            ┌──────────┐
                     ┌──────│ PENDING  │◄─────────────────────────────────┐
                     │      └────┬─────┘                                  │
                     │           │                                        │
                DEV or QA    DEV or PM                                    │
                 CLAIMS       ASSIGNS                                     │
                     │           │                                        │
                     ▼           ▼                                        │
                            ┌──────────┐                                  │
                            │ CLAIMED  │ ◄─── Agent owns it               │
                            └────┬─────┘                                  │
                                 │                                        │
                            DEV STARTS                                    │
                                 │                                        │
                                 ▼                                        │
                         ┌─────────────┐                                  │
              ┌──────────│ IN_PROGRESS │──────────┐                       │
              │          └──────┬──────┘          │                       │
              │                 │                 │                       │
           BLOCKED           PAUSED            WORKING                    │
              │                 │                 │                       │
              ▼                 ▼                 │                       │
         ┌──────────┐      ┌─────────┐            │                       │
         │ BLOCKED  │      │ PAUSED  │            │                       │
         └────┬─────┘      └────┬────┘            │                       │
              │                 │                 │                       │
            UNBLOCK           RESUME              │                       │
              │                 │                 │                       │
              └────────►───────►└─────────►───────┘                       │
                                 │                                        │
                          DEV VERIFIES                                    │
                                 │                                        │
                                 ▼                                        │
                          ┌───────────┐                                   │
                          │ VERIFYING │ ◄─── Self-check                   │
                          └─────┬─────┘                                   │
                                │                                         │
                          DEV SUBMITS QA                                  │
                                │                                         │
                                ▼                                         │
                         ┌─────────────┐                                  │
                         │ AWAITING_QA │ ◄─── QA picks up                 │
                         └──────┬──────┘                                  │
                                │                                         │
                    ┌───────────┴───────────┐                             │
                    │                       │                             │
                 QA PASS                 QA FAIL                          │
                    │                       │                             │
                    ▼                       ▼                             │
    ┌───────────────────────────┐   ┌─────────────────┐                   │
    │ AWAITING_DOCUMENTATION    │   │ NEEDS_REVISION  │───────────────────┘
    └───────────┬───────────────┘   └─────────────────┘
                │                        (back to dev)
         DOCS COMPLETE
                │
                ▼
       ┌────────────────────┐
       │ AWAITING_PM_REVIEW │ ◄─── PM final review
       └─────────┬──────────┘
                 │
            PM COMPLETES
                 │
                 ▼
            ┌───────────┐
            │ COMPLETED │
            └───────────┘
```

## Status Definitions

| Status | Description | Who Owns It |
|--------|-------------|-------------|
| `backlog` | PM is setting up the task | PM |
| `pending` | Ready for someone to claim | Unassigned or pre-assigned |
| `claimed` | Agent has taken ownership | Developer/QA/Documenter |
| `in_progress` | Active work happening | Developer/QA/Documenter |
| `blocked` | Waiting on another task | Developer |
| `paused` | Temporarily stopped | Developer |
| `verifying` | Developer self-checking | Developer |
| `awaiting_qa` | Ready for QA review | QA |
| `needs_revision` | QA found issues | Developer |
| `awaiting_documentation` | QA passed, needs docs | Documenter |
| `awaiting_pm_review` | Docs done, PM reviews | PM |
| `completed` | Done | - |
| `cancelled` | Cancelled | - |

## Transition Rules

### Who Can Trigger What

| Transition | Triggered By | Tool |
|------------|--------------|------|
| backlog → pending | PM | `roboco_task_activate()` |
| pending → claimed | Any agent | `roboco_task_claim()` |
| claimed → in_progress | Owner | `roboco_task_start()` |
| in_progress → blocked | Owner | `roboco_task_block()` |
| in_progress → paused | Owner | `roboco_task_pause()` |
| blocked → in_progress | Owner or PM | `roboco_task_unblock()` |
| paused → in_progress | Owner | `roboco_task_start()` (resume) |
| in_progress → verifying | Developer | `roboco_task_submit_verification()` |
| verifying → awaiting_qa | Developer | `roboco_task_submit_qa()` |
| awaiting_qa → claimed | QA | `roboco_task_claim()` |
| awaiting_qa → awaiting_documentation | QA | `roboco_task_qa_pass()` |
| awaiting_qa → needs_revision | QA | `roboco_task_qa_fail()` |
| needs_revision → claimed | Developer | `roboco_task_claim()` |
| awaiting_documentation → claimed | Documenter | `roboco_task_claim()` |
| awaiting_documentation → awaiting_pm_review | Documenter | `roboco_task_docs_complete()` |
| awaiting_pm_review → completed | PM | `roboco_task_complete()` |
| any → cancelled | PM | `roboco_task_cancel()` |
| in_progress → pending/blocked/awaiting_qa | Owner | `roboco_task_substitute()` |
| in_progress → awaiting_pm_review | Any agent | `roboco_task_submit_pm_review()` |

## What Each Role Can Claim

| Role | Can Claim From |
|------|----------------|
| Developer | `pending`, `needs_revision` |
| QA | `awaiting_qa` |
| Documenter | `pending`, `awaiting_documentation` |
| PM | `pending`, `backlog` |

## Blocking Rules

An agent **CANNOT claim a new task** if they have:
- A task in `in_progress`
- A task in `claimed` (should start it first)
- A task in `verifying` (should submit to QA first)

An agent **CAN claim** even if they have:
- A task in `paused` (can work on something else while waiting)
- A task in `blocked` (can work on something else while waiting)

**Exception:** If claiming a task already assigned to them (PM pre-assigned), the blocking check is skipped for THAT specific task.

## Substitution (Graceful Exit)

Agents can **substitute out** of a task when they cannot continue:

```
roboco_task_substitute(task_id, reason, details)
```

| Reason | New Status | When to Use |
|--------|------------|-------------|
| `low_context` | pending | Insufficient context to continue safely |
| `out_of_scope_team` | pending | Task belongs to different team |
| `out_of_scope_role` | pending | Task requires different role (QA, not dev) |
| `task_complete` | awaiting_qa | Finished work, releasing for next stage |
| `max_retries` | pending | Exceeded retry limit, need fresh perspective |
| `blocked_external` | blocked | Need skills outside your capabilities |

**Key:** Substitution BYPASSES the "can't claim while in_progress" rule. After substituting, you are free to claim new work.

## Direct PM Submission (Alternate Path)

For non-dev tasks that don't need QA review:

```
roboco_task_submit_pm_review(task_id, notes)
```

Status: `in_progress → awaiting_pm_review`

Use for: validation tasks, audits, research, or any task assigned directly that doesn't produce code.

## Automatic PM Assignment

The system automatically assigns tasks to the responsible PM and sends notifications in these cases:

| Trigger | New Status | PM Assigned | Notification |
|---------|------------|-------------|--------------|
| `roboco_task_docs_complete()` | awaiting_pm_review | Cell PM (or Main PM) | ✅ task_assignment |
| `roboco_task_submit_pm_review()` | awaiting_pm_review | Cell PM (or Main PM) | ✅ task_assignment |
| `roboco_task_substitute()` with `task_complete` (QA/Documenter) | awaiting_pm_review | Cell PM | ✅ task_assignment |
| `roboco_task_unblock()` | in_progress | (unchanged) | ✅ to assigned agent |

**PM Resolution Chain:**
1. Get PM for the agent's role (QA → Cell PM, Cell PM → Main PM)
2. Fallback to team PM (backend → be-pm, frontend → fe-pm)
3. Task is assigned to resolved PM's UUID
4. Real-time notification delivered via Redis Streams
