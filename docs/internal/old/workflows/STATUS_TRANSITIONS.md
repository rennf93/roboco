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
         ┌───────┴───────┐
         │               │
    PM COMPLETES    PM ESCALATES
    (simple tasks)  (major tasks)
         │               │
         │               ▼
         │    ┌────────────────────────┐
         │    │ AWAITING_CEO_APPROVAL  │ ◄─── CEO final decision
         │    └──────────┬─────────────┘
         │               │
         │       ┌───────┴───────┐
         │       │               │
         │   CEO APPROVES    CEO REJECTS
         │       │               │
         │       │               ▼
         │       │       ┌─────────────────┐
         │       │       │ NEEDS_REVISION  │───────► (back to dev)
         │       │       └─────────────────┘
         │       │
         └───────┴───────┐
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
| `awaiting_ceo_approval` | Major task, CEO decides | CEO |
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
| awaiting_pm_review → awaiting_ceo_approval | PM | `roboco_task_escalate_to_ceo()` |
| awaiting_ceo_approval → completed | CEO | `roboco_task_ceo_approve()` |
| awaiting_ceo_approval → needs_revision | CEO | `roboco_task_ceo_reject()` |
| any → cancelled | PM/CEO | `roboco_task_cancel()` |
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

## CEO Approval Workflow

For major tasks that require executive sign-off, PMs can escalate to CEO:

### When to Escalate to CEO

| Task Type | Escalate? | Reason |
|-----------|-----------|--------|
| Parent task with subtasks | ✅ Yes | Aggregate work needs final approval |
| Breaking changes | ✅ Yes | Architectural impact |
| High-priority features | ✅ Yes | Business-critical |
| Security changes | ✅ Yes | Risk assessment |
| Simple bug fixes | ❌ No | PM can complete directly |
| Documentation updates | ❌ No | PM can complete directly |

### Escalation Flow

```
PM reviews task in awaiting_pm_review
        │
        ├─── Simple task? → roboco_task_complete() → COMPLETED
        │
        └─── Major task? → roboco_task_escalate_to_ceo(notes) → AWAITING_CEO_APPROVAL
                                    │
                                    ▼
                            CEO receives notification
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
            CEO APPROVES                      CEO REJECTS
    roboco_task_ceo_approve()         roboco_task_ceo_reject(reason)
                    │                               │
                    ▼                               ▼
               COMPLETED                     NEEDS_REVISION
                                          (back to developer)
```

### CEO Actions (Human-in-the-Loop)

The CEO is human and receives notifications when tasks are escalated. CEO uses the API directly:

| API Endpoint | Description |
|--------------|-------------|
| `POST /api/v1/tasks/{id}/ceo-approve` | Approve and complete the task |
| `POST /api/v1/tasks/{id}/ceo-reject` | Reject and send back for revision |

**Note:** PM escalation automatically sends a high-priority notification to the CEO.

## Git Integration

All tasks follow the git workflow alongside the task lifecycle.

### Git Workflow Overview

```
PM creates branch ──► Dev checkouts ──► Dev commits ──► Dev pushes ──► QA reviews on branch
                                                                              │
                                                                              ▼
                                                                    [QA PASSES]
                                                                              │
                                                    ┌─────────────────────────┴─────────────────────────┐
                                                    │         PARALLEL EXECUTION                        │
                                                    │         (awaiting_documentation)                  │
                                                    │                                                   │
                                                    ▼                                                   ▼
                                              Documenter                                           Developer
                                            writes docs ──►                                     reviews all ──►
                                            commits docs ──►                                    creates PR ──►
                                            docs_complete=True                                  pr_created=True
                                                    │                                                   │
                                                    └───────────────────┬───────────────────────────────┘
                                                                        │
                                                              [BOTH complete]
                                                                        │
                                                                        ▼
                                                              awaiting_pm_review
                                                                        │
                                                              PM(s) review PR
                                                                        │
                                                                        ▼
                                                            awaiting_ceo_approval
                                                                        │
                                                              CEO merges PR
                                                                        │
                                                                        ▼
                                                                   completed
```

### Branch Naming Convention

```
{reason}/{team}/{task-id}[/{subtask-id}]
```

| Reason | Description |
|--------|-------------|
| `feature` | New functionality |
| `bug` | Bug fix |
| `chore` | Maintenance, refactoring |
| `docs` | Documentation-only changes |
| `hotfix` | Urgent production fix |

**Examples:**
- `feature/backend/abc123` - Parent task branch
- `feature/backend/abc123/xyz789` - Subtask branch
- `bug/frontend/def456` - Bug fix branch

### Git Operations by Role

| Role | Git Operations |
|------|---------------|
| **Main PM** | Create parent branches from `main` |
| **Cell PM** | Create subtask branches from parent, merge subtask PRs |
| **Developer** | Checkout, commit, push, create PR |
| **QA** | Read-only (review code on branch) |
| **Documenter** | Read + commit docs to branch |
| **CEO** | Merge final PR to `main` |

### Parallel Execution in `awaiting_documentation`

When QA passes, the task enters `awaiting_documentation`. Two things happen in parallel:

1. **Documenter** is notified and writes documentation, commits to branch
2. **Developer** is notified and reviews everything, creates the PR

The task has two flags:
- `docs_complete` - Set when documenter calls `roboco_task_docs_complete()`
- `pr_created` - Set when developer calls `roboco_git_create_pr()`

**The task only moves to `awaiting_pm_review` when BOTH flags are true.**

### Hierarchical Branch Strategy

```
main
│
└── feature/backend/ABC123                   ← Main PM creates (parent task)
    │                                           Forks from: main
    │
    ├── feature/backend/ABC123/XYZ789        ← Cell PM creates (subtask)
    │       │                                   Forks from: parent branch
    │       └── MERGES INTO: parent branch ↑   (Cell PM approves)
    │
    └── feature/backend/ABC123/QRS456        ← Cell PM creates (subtask)
            │                                   Forks from: parent branch
            └── MERGES INTO: parent branch ↑   (Cell PM approves)

When all subtasks merged:
    feature/backend/ABC123 ──► PR to main ──► Main PM + Cell PM review ──► CEO merges
```

### Git Tools Reference

**Read-Only (All Agents):**
- `roboco_git_status(project_slug)` - Branch and file status
- `roboco_git_log(project_slug, limit)` - Commit history
- `roboco_git_branch_list(project_slug)` - List branches
- `roboco_git_diff(project_slug, staged)` - View changes

**Developer:**
- `roboco_git_commit(project_slug, message, task_id)` - Create commit
- `roboco_git_push(project_slug, task_id)` - Push to remote
- `roboco_git_create_pr(project_slug, task_id, title, body)` - Create PR

**PM:**
- `roboco_git_create_branch(project_slug, task_id, branch_type, parent_branch)` - Create branch
- `roboco_git_checkout(project_slug, branch)` - Switch branches
- `roboco_git_merge_pr(project_slug, pr_number, task_id, merge_method)` - Merge PR
