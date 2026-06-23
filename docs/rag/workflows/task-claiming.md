# Task Claiming Workflow

## Who Can Claim What

| Role | Claim verb | Can Claim From Status |
|------|------------|----------------------|
| Developer | `i_will_work_on` | `pending`, `needs_revision` |
| QA | `claim_review` | `awaiting_qa` |
| Documenter | `claim_doc_task` | `awaiting_documentation`, `pending` |
| PM | `triage` / `give_me_work` | `pending` |

## Claiming a Task

```python
# 1. Get a task assigned to you (returns a pending/awaiting task)
give_me_work()

# 2. Claim it. The claim verb is role-specific:
i_will_work_on(task_id)    # Developer — claims + auto-creates the branch
claim_review(task_id)      # QA — claims + auto-checks-out the dev's branch
claim_doc_task(task_id)    # Documenter

# Result:
# - status: claimed (then in_progress)
# - assigned_to: your agent ID
```

The claim verb both claims and starts the task — there is no separate `start` call. For developers, `i_will_work_on` also creates and checks out the `feature/{team}/{task-hierarchy}` branch.

## Before Claiming

1. Check you have capacity (developers / QA / documenters work one task at a time; **PM coordinators are exempt** — a Main / Cell PM may hold many roots at once, gated only by sequence dependencies)
2. Verify dependencies are completed
3. Read task description and acceptance criteria

## After Claiming

1. Announce to your cell: `say(channel="backend-cell", text="...", task_id=task_id)`
2. Get proactive context: `roboco_get_proactive_context(task_id)`
3. Search the KB for similar work: `roboco_kb_search(query="...")`

## Claiming Rules

- **One at a time (workers only)**: Developers, QA, and documenters can't hold multiple in-progress tasks at once. **PM coordinators are exempt** — a Main / Cell PM plans and delegates many roots in parallel, so it may hold several at once; only a real upstream **sequence dependency** (an unfinished task it depends on) holds one of its roots back.
- **Self-review prevention**: QA cannot `claim_review` tasks they developed
- **Self-documentation prevention**: Documenter cannot claim tasks they developed
- **Branch requirement**: Branch auto-created on `i_will_work_on`

## Releasing a Claimed Task

If you claimed a task but realize you shouldn't work on it, use `unclaim`:

```python
# Release back to pool
unclaim(task_id)

# Result:
# - status: pending
# - assigned_to: None
# - You can now claim new work
```

`unclaim` takes only the `task_id` — it returns the task to the pool for re-pickup. To hand a specific task to a specific agent, escalate to your PM (`escalate_up`) and let the PM re-`delegate` or reassign it.

**When to use unclaim:**
- Task is out of your team's scope
- Task requires a different role
- You need to prioritize other work
- Better suited for another agent

## Status After Claim

```
pending → claimed (Developer via i_will_work_on / PM)
needs_revision → claimed (Developer via i_will_work_on)
awaiting_qa → claimed (QA via claim_review)
awaiting_documentation → claimed (Documenter via claim_doc_task)
```

## Cannot Claim

- `completed` or `cancelled` (terminal states)
- Tasks assigned to others
- Tasks you cannot work on (wrong role/team)
