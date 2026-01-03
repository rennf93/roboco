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

**Error**: "Cannot review own work"

**Cause**: QA/Documenter trying to claim task they developed

**Solution**: Another QA/Documenter must handle this task

## Git Task: No Branch

**Error**: "Branch name required for git tasks"

**Cause**: PM hasn't created branch yet

**Solution**: Wait for PM or ask PM to create branch:
```python
roboco_git_create_branch(project_slug, task_id, "feature")
```
