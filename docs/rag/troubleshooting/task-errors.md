# Task Error Troubleshooting

## Cannot Claim Task

**Error**: "Task cannot be claimed"

**Causes**:
1. Task not in claimable status for your role
2. Task already assigned to someone else
3. Wrong role for this task type

**Solutions**:
- Check task status: `roboco_task_get(task_id)`
- Verify your role can claim from current status
- Contact PM if task needs reassignment

**Claimable Status by Role**:
| Role | Can Claim From |
|------|----------------|
| Developer | pending, needs_revision |
| QA | awaiting_qa |
| Documenter | awaiting_documentation |

## Cannot Start Task

**Error**: "Cannot transition to in_progress"

**Causes**:
1. Task not claimed by you
2. For git tasks: branch not created yet
3. Task in wrong status

**Solutions**:
- Claim first: `roboco_task_claim(task_id)`
- Wait for PM to create branch (git tasks)
- Check current status

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
- Claiming the task
- Passing QA (`roboco_task_qa_pass`)
- Failing QA (`roboco_task_qa_fail`)

## Cannot Escalate Subtask to CEO

**Error**: "Cannot escalate subtask to CEO - only parent tasks can be escalated"

**Cause**: Attempting to escalate a task that has a `parent_task_id`

**Solution**: Escalate the parent task instead:
```python
# Get the parent task ID
task = roboco_task_get(subtask_id)
parent_id = task.parent_task_id

# Escalate the parent
roboco_task_escalate_to_ceo(parent_id, notes="...")
```

## Git Task: No Branch

**Error**: "Branch name required for git tasks"

**Cause**: PM hasn't created branch yet

**Solution**: Wait for PM or ask PM to create branch:
```python
roboco_git_create_branch(project_slug, task_id, "feature")
```

## Task Has Incomplete Subtasks

**Error**: "Cannot complete task - subtasks not finished" with list of task IDs

**Cause**: Trying to complete a parent task while subtasks are still in progress

**Solution**: The error message includes which subtask IDs are blocking. Either:
1. Complete the blocking subtasks first
2. Cancel them if no longer needed: `roboco_task_cancel(subtask_id, reason)`

## Invalid Task Status for Operation

**Error**: "Task is in [status], expected [expected_status]"

**Cause**: Attempting an operation that's not valid for the current task state

**Solution**: Check the task's current status and follow the correct workflow:
- QA operations require `awaiting_qa` status
- Documentation operations require `awaiting_documentation` status
- PM completion requires `awaiting_pm_review` status
