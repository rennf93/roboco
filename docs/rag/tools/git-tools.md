# Git Tools

## Read Operations

| Tool | Purpose |
|------|---------|
| `roboco_git_status` | View working tree status |
| `roboco_git_log` | View commit history |
| `roboco_git_branch_list` | List branches |
| `roboco_git_diff` | View changes |

## Status and Diff

```python
# Check status
status = roboco_git_status(project_slug="roboco")

# View changes
diff = roboco_git_diff(project_slug="roboco")

# View history
log = roboco_git_log(
    project_slug="roboco",
    branch="feature/backend/a1b2c3d4"
)
```

## Branch Operations

**Branches are auto-created when tasks are claimed:**
- Root task claim → `feature/team/ROOT_ID`
- Subtask claim → `feature/team/ROOT_ID/SUB_ID`
- Sub-subtask claim → `feature/team/ROOT_ID/SUB_ID/SUBSUB_ID`

No manual branch creation needed.

```python
# List branches
branches = roboco_git_branch_list(project_slug="roboco")

# Checkout branch (if needed)
roboco_git_checkout(
    project_slug="roboco",
    branch="feature/backend/a1b2c3d4"
)
```

## Commit and Push

```python
# Commit with task link
roboco_git_commit(
    project_slug="roboco",
    task_id=task_id,
    message="Add rate limiting endpoint",
    commit_type="feat"  # Required
)
# Creates: [a1b2c3d4] feat: Add rate limiting endpoint

# Push to remote
roboco_git_push(project_slug="roboco", task_id=task_id)
```

## Pull Requests

```python
# Create PR
roboco_git_create_pr(
    project_slug="roboco",
    task_id=task_id,
    title="[TASK-a1b2c3d4] Add rate limiting",
    body="## Summary\n..."
)

# Merge PR (PM only)
roboco_git_merge_pr(
    project_slug="roboco",
    pr_number=123,
    task_id=task_id,
    merge_method="squash"  # squash, merge, rebase
)
```

## Branch Naming

```
{type}/{team}/{task-id-prefix}
```

| Type | Use |
|------|-----|
| `feature/` | New functionality |
| `bug/` | Bug fixes |
| `chore/` | Maintenance |
| `docs/` | Documentation |
| `hotfix/` | Urgent fixes |
