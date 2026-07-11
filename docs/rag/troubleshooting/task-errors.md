# Task Error Troubleshooting

## Cannot Claim Task

**Error**: "Task cannot be claimed"

**Causes**:
1. Task not in claimable status for your role
2. Task already assigned to someone else
3. Wrong role for this task type

**Solutions**:
- Check what's actionable for you: `give_me_work()` (or `triage()` for PMs)
- Verify your role can claim from the task's current status
- Contact PM if task needs reassignment

**Claimable Status by Role**:
| Role | Can Claim From | Verb |
|------|----------------|------|
| Developer | pending, needs_revision | `i_will_work_on(task_id)` |
| QA | awaiting_qa | `claim_review(task_id)` |
| Documenter | pending, awaiting_documentation | `claim_doc_task(task_id)` |
| Cell PM / Main PM | pending | `i_will_plan(task_id)` |

## Wrong Team

**Error**: "team 'X' may not act on a 'Y' task (team-based restriction)" (`not_authorized`)

**Cause**: You are a cell-scoped role (developer, QA, documenter, cell PM) and the task belongs to another team. Claim, resume, unblock, and activate are all team-matched.

**Solutions**:
- Follow the envelope's `remediate`: call `give_me_work()` to find a task in your own team
- If the task was misrouted to you, leave it — the pool re-routes it to the right team

Org-wide roles (Main PM, Board, CEO, PR reviewer) are exempt — they act across cells by design.

## Cannot Start Task

**Error**: "Cannot transition to in_progress"

**Causes**:
1. Task not claimed by you
2. Task in wrong status

**Solutions**:
- Claim + start in one step: `i_will_work_on(task_id, plan="...")` (devs), `claim_review(task_id)` (QA), `claim_doc_task(task_id)` (doc), or `i_will_plan(task_id, plan, approach)` (PMs)
- Check current status

Note: Git branches are auto-created on claim, no waiting needed.

## Cannot Submit for QA

**Error**: "Invalid transition from current status"

**Causes**:
1. Task not in `in_progress` or `verifying`
2. Missing required fields

**Solutions**:
- Move through verification first
- Ensure task is actively being worked

## Self-Review Prevented

**Error**: "Cannot review own work" or "SELF_REVIEW_NOT_ALLOWED"

**Cause**: QA trying to claim, pass, or fail a task they originally developed

**Solution**: Another QA must handle this task. Self-review prevention applies to:
- Claiming the task (`claim_review`)
- Passing QA (`pass`)
- Failing QA (`fail`)

## Cannot Escalate Subtask to CEO

**Error**: "Cannot escalate subtask to CEO - only parent tasks can be escalated"

**Cause**: Attempting to escalate a task that has a `parent_task_id`

**Solution**: Escalate the parent task instead. Find the parent task ID (it's on the subtask's `parent_task_id` field, surfaced in your `give_me_work()` / `triage()` envelope), then escalate the parent:
```python
escalate_to_ceo(task_id=parent_id, reason="...")
```
`escalate_to_ceo` is Main PM / Board only; Cell PMs and cell members use `escalate_up(task_id, reason)` instead.

## Git Task: Parent Branch Required

**Error**: "Parent task must be claimed first to create its branch"

**Cause**: Trying to claim a subtask when parent task hasn't been claimed yet

**Solution**: Parent task must be claimed first. Branches are auto-created hierarchically:
1. Parent is claimed → parent branch created
2. Then subtask can be claimed → subtask branch created (forked from parent)

## Task Has Incomplete Subtasks

**Error**: "Cannot complete task - subtasks not finished" with list of task IDs

**Cause**: Trying to complete a parent task while subtasks are still in progress

**Solution**: The error message includes which subtask IDs are blocking. Either:
1. Complete the blocking subtasks first (drive them through QA → docs → `complete(task_id, notes)`)
2. Cancel them if no longer needed (PM/CEO only — cancellation is not an agent verb; ask your PM)

## Reflow/Formatting Task Has Zero Diff

**Symptom**: A task asks you to reflow specific markdown file(s) so they pass `make reflow-check`, but `python3 scripts/reflow_md.py --check` already reports "OK" with no changes, so there is no diff to commit — and `i_am_done` hard-requires at least one commit.

**Cause**: `scripts/reflow_md.py` walks the whole repo tree (`ROOT.rglob("*.md")`) on every invocation; it does not scope to positional file-path arguments passed on the command line, so `--check docs/foo.md` and a bare `--check` do the same repo-wide scan. If the target file(s) were already reflowed upstream (e.g. in an earlier commit on the same branch chain), the task is satisfied-by-upstream with a structurally empty diff.

**Solution**: Verify with the repo-wide check (not a scoped one, since scoping is a no-op) and record the zero-diff finding in a `decision` journal entry. If the task's acceptance criteria are already met with nothing left to change in the named files, do not keep re-running the same check — escalate once with the concrete verification so a PM can either stamp the task as satisfied-by-upstream/cancel it, or direct a small verification commit outside the target file(s) to satisfy the commit gate.

## Invalid Task Status for Operation

**Error**: "Task is in [status], expected [expected_status]"

**Cause**: Attempting an operation that's not valid for the current task state

**Solution**: Check the task's current status and follow the correct workflow:
- QA operations require `awaiting_qa` status
- Documentation operations require `awaiting_documentation` status
- PM completion requires `awaiting_pm_review` status
