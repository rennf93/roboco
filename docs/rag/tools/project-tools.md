# Project Tools

## Overview

Project tools manage git repositories and agent workspaces.

## List Projects

```python
roboco_project_list()           # All accessible projects
roboco_project_list(cell="backend")  # Filter by cell
```

Returns projects you have access to (cell-scoped for non-PMs).

## Get Project Details

```python
roboco_project_get(slug="roboco")
```

Returns: `name`, `git_url`, `assigned_cell`, `default_branch`, `test_command`, etc.

## Create Project (PM+ Only)

```python
roboco_project_create(
    name="RoboCo Panel",
    slug="roboco-panel",
    git_url="git@github.com:org/roboco-panel.git",
    assigned_cell="frontend",
    default_branch="main",
    test_command="pnpm test",
    lint_command="pnpm lint"
)
```

**Who can create:** Main PM, Board, CEO

## Update Project

```python
roboco_project_update(
    slug="roboco-panel",
    test_command="pnpm test:ci",
    lint_command="pnpm lint:fix"
)
```

**Who can update:**
- CEO, Main PM: Any project
- Cell PM: Own cell's projects only

## Workspace Tools

### Ensure Workspace

```python
roboco_workspace_ensure(project_slug="roboco")
```

Creates your workspace if it doesn't exist. Auto-clones the repository.

### Check Workspace Status

```python
roboco_workspace_status(project_slug="roboco")
```

Returns: `exists`, `branch`, `has_uncommitted`, `staged_files`, `unstaged_files`

### List Workspaces (PM Only)

```python
roboco_workspace_list(project_slug="roboco")
```

Lists all agent workspaces for a project. Cell PM sees own cell only.

## Permission Matrix

| Tool | Dev/QA/Doc | Cell PM | Main PM | CEO |
|------|------------|---------|---------|-----|
| `project_list` | Own cell | Own cell | All | All |
| `project_get` | Yes | Yes | Yes | Yes |
| `project_create` | No | No | Yes | Yes |
| `project_update` | No | Own cell | All | All |
| `workspace_ensure` | Yes | Yes | Yes | Yes |
| `workspace_status` | Yes | Yes | Yes | Yes |
| `workspace_list` | No | Own cell | All | All |

## Task Creation with Project

When creating git-enabled tasks:

```python
roboco_task_create(
    title="Add rate limiting",
    team="backend",
    project_slug="roboco",    # Required for git tasks
    requires_git=True         # Default: True
)
```

Use `project_slug="roboco"` for internal RoboCo codebase work.
