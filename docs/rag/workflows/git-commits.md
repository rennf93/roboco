# Git Commit Workflow

## Commit Format

All commits are automatically prefixed with the task ID by the choreographer:

```
[{task-id-prefix}] {message}
```

Example: `[a1b2c3d4] Add rate limiting endpoint`

You write the message — the prefix is added for you. Don't include `[task-id]` yourself; it gets stripped and re-applied.

## Who Can Commit

`commit` is in the **roboco-do** MCP and is mounted only for **developers** and **documenters**. PMs delegate code work and call `complete` to merge.

There is **no** `roboco_git_commit / _push / _create_pr` MCP tool. The single `commit` verb covers commit + push + PR-trigger via the choreographer.

## Creating Commits

```python
commit(
    message="Add rate limiting endpoint",
    files=["roboco/api/routes/rate.py"],  # optional; defaults to all staged
)
```

This automatically:

1. Prefixes the commit with `[task-id-first-8-chars]`
2. Validates the message via `commit_validator`
3. Stages the listed files (or everything tracked + modified if omitted)
4. Pushes to the agent's auto-created branch
5. Records the commit on the task (`commits[]` field on `TaskTable`)
6. Opens a PR through the choreographer when the task transitions out of `in_progress` (no separate `create_pr` call required)

## Before Committing

1. Run tests: `uv run pytest` or `pnpm test`
2. Run linter: `uv run ruff check .` or `pnpm lint`
3. Run type check: `uv run mypy roboco/` or `pnpm typecheck`
4. Format code: `uv run ruff format .` or `pnpm format`

## After Committing

You don't push or create a PR yourself. The choreographer pushed the commit during `commit()`, and the PR is opened/merged as part of the lifecycle transitions:

- `open_pr(task_id)` — opens the PR (devs)
- `pass(task_id)` (QA) → `i_documented(task_id)` (doc) → `complete(task_id)` (cell PM merges the leaf PR; main PM opens the master PR)

## Viewing Commits and History

```python
# Read-only inspection (any role) — roboco-git-readonly MCP
status = roboco_git_status(project_slug="roboco")
log = roboco_git_log(project_slug="roboco", branch="feature/backend/a1b2c3d4--def67890")
diff = roboco_git_diff(project_slug="roboco")
branches = roboco_git_branch_list(project_slug="roboco")
```
