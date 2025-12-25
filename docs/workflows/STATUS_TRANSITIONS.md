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
