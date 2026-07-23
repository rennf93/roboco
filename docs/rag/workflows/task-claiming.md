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

The claim verb both claims and starts the task — there is no separate `start` call. For developers, `i_will_work_on` also creates the `feature/{team}/{task-hierarchy}` branch and **adds a dedicated per-task worktree** at `{clone_root}/.worktrees/{task-id-first-8}/`, checking out the branch there. Your container is started with that worktree as its cwd, and the clone root's HEAD is never moved by the claim — so a second claim (or a coordinator PM's many parallel roots) never overwrites your first task's uncommitted work. See `docs/rag/architecture/workspaces.md` for the worktree model.

Exactly one active WorkSession exists per task at a time (enforced in the service layer and by a DB unique index). A re-claim — pool release, reaper unclaim, escalation redirect — supersedes any prior agent's stale active session for that task and re-points the worktree at the new claim.

If your task has `dependency_ids` in the same repo, the fresh branch cut also backfills each dependency's already-landed work when it sits outside your branch's own ancestor chain (a same-parent sibling or a same-project wave usually doesn't need this — the shared base already has it; a cross-subtree or cross-cell dependency edge can). This is a content assist, not a gate: a clean merge is silent, and a real conflict aborts the merge (your branch is left exactly at its cut point) and appends a note to the task naming the conflicting branch and files — resolve it by hand (merge the named branch into yours) before assembling your PR. A cross-repo dependency has no shared git history and is skipped entirely.

## Before Claiming

1. Check you have capacity (developers / QA / documenters work one task at a time; **PM coordinators are exempt** — a Main / Cell PM may hold many roots at once, gated only by sequence dependencies)
2. Verify dependencies are completed AND no lower-sequence sibling is still open (see Claiming Rules below)
3. Read task description and acceptance criteria

## After Claiming

1. Get proactive context: `roboco_get_proactive_context(task_id)`
2. Search the KB for similar work: `roboco_kb_search(query="...")`

## Claiming Rules

- **One at a time (workers only)**: Developers, QA, and documenters can't hold multiple in-progress tasks at once. A **blocked** task still counts as active — a blocked dev cannot `claim` a second task; unblock or `unclaim` first. **PM coordinators are exempt** — a Main / Cell PM plans and delegates many roots in parallel, so it may hold several at once; only a real upstream **sequence dependency** (an unfinished task it depends on) holds one of its roots back.
- **Team match**: cell-scoped roles (developer, QA, documenter, cell PM) are rejected `not_authorized` on another team's tasks — claim, resume, unblock, and activate are all team-matched. The `remediate` hint says it: call `give_me_work()` to find a task in your own team. Org-wide roles (Main PM, Board, CEO, PR reviewer) are exempt.
- **Self-review prevention**: QA cannot `claim_review` tasks they developed
- **Self-documentation prevention**: Documenter cannot claim tasks they developed
- **Branch requirement**: Branch auto-created on `i_will_work_on`
- **Sequence order (strict, assignee-blind)**: if a task has a parent and a `sequence` number, it cannot be claimed while any sibling with a strictly lower sequence is still non-terminal — regardless of who owns which task. Siblings on the SAME sequence run in parallel (independent work ties at 0, or at the wave a delegating PM stamped from the collision graph). This is independent of, and stricter than, `dependency_ids`: a claim attempt on a sequence-held task fails even with no unmet dependency. The error names the blocking sibling by title — `unclaim`/wait is the only remedy, there is no override verb. The dispatcher pre-filters sequence-held (and dependency-held) tasks before attempting a claim, so you should rarely see this in practice — but a claim you make directly (rather than via `give_me_work`) can still hit it.
- **Project budget cap** (when task budgets are armed): `i_will_work_on` / `i_will_plan` are refused once the project's `monthly_budget_usd` has been reached this calendar month — a WORK-STARTING claim only, so a QA/doc/PR-review/PM-merge claim on already-in-flight work is never blocked by this. There is no override; wait for the next month or ask the CEO to raise the cap.

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
awaiting_qa → awaiting_qa (QA via claim_review — claim recorded, status stays put so pass/fail match)
awaiting_documentation → claimed (Documenter via claim_doc_task)
```

## Cannot Claim

- `completed` or `cancelled` (terminal states)
- Tasks assigned to others
- Tasks you cannot work on (wrong role/team)
