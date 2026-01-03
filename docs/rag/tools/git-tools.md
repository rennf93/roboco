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

```python
# List branches
branches = roboco_git_branch_list(project_slug="roboco")

# Create branch (PM only)
roboco_git_create_branch(
    project_slug="roboco",
    task_id=task_id,
    branch_type="feature"  # feature, fix, refactor, docs
)
# Creates: feature/backend/a1b2c3d4

# Checkout branch
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
    message="Add rate limiting endpoint"
)
# Creates: [a1b2c3d4] Add rate limiting endpoint

# Push to remote
roboco_git_push(project_slug="roboco")
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
| `fix/` | Bug fixes |
| `refactor/` | Code restructuring |
| `docs/` | Documentation |
