# Git Commit Workflow

## Commit Format

All commits are automatically prefixed with task ID:

```
[{task-id-prefix}] {message}
```

Example:
```
[a1b2c3d4] Add rate limiting endpoint
```

## Creating Commits

```python
roboco_git_commit(
    project_slug="roboco",
    task_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    message="Add rate limiting endpoint"
)
```

This automatically:
1. Prefixes commit with task ID (first 8 chars)
2. Records commit in task's commit history
3. Links to work session

## Commit Message Types

| Type | Description |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `style` | Formatting |
| `refactor` | Code restructure |
| `test` | Tests |
| `chore` | Maintenance |
| `perf` | Performance |

## Full Commit Format

```
{type}({scope}): {description}

{body}

Task: {task-id}
Co-authored-by: {agent-name}
```

## Before Committing

1. Run tests: `uv run pytest` or `pnpm test`
2. Run linter: `uv run ruff check .` or `pnpm lint`
3. Run type check: `uv run mypy roboco/` or `pnpm typecheck`
4. Format code: `uv run ruff format .` or `pnpm format`

## Push Commits

```python
roboco_git_push(project_slug="roboco")
```

Push before:
- Submitting for QA
- Creating PR
- Ending work session

## Viewing Commits

```python
# View commit history
roboco_git_log(project_slug="roboco", branch="feature/backend/a1b2c3d4")

# View changes
roboco_git_diff(project_slug="roboco")
```
