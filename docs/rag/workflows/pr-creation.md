# Pull Request Creation

## When to Create PR

Create PR in `awaiting_documentation` phase (parallel with documenter).

## Creating a PR

```python
roboco_git_create_pr(
    project_slug="roboco",
    task_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    title="[TASK-a1b2c3d4] Add rate limiting",
    body="## Summary\n- Implemented sliding window...\n\n## Test Plan\n..."
)
```

This automatically:
1. Creates PR via GitHub CLI (`gh pr create`)
2. Targets project's default branch
3. Sets `pr_created=True` on task
4. Records PR number and URL

## PR Title Format

```
[TASK-{id-prefix}] {description}
```

Example: `[TASK-a1b2c3d4] Add rate limiting endpoint`

## PR Body Template

```markdown
## Summary
- What was implemented
- Key changes

## Test Plan
- How to test the changes
- Test coverage

Task: {task-id}
```

## Parallel Execution

In `awaiting_documentation`:

| Agent | Action | Flag |
|-------|--------|------|
| Developer | Creates PR | `pr_created=True` |
| Documenter | Writes docs | `docs_complete=True` |

Task advances to `awaiting_pm_review` when BOTH are done.

## Before Creating PR

1. Push all commits: `roboco_git_push()`
2. Verify tests pass
3. Ensure code quality checks pass
4. Branch is up to date with target

## PM Merges PR

After completing task:
```python
roboco_git_merge_pr(
    project_slug="roboco",
    pr_number=123,
    merge_method="squash"  # or "merge", "rebase"
)
```

Only PM can merge PRs.
