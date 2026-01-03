# Workspace Structure

## Multi-Agent Isolation

Each agent gets their own git clone:

```
{workspaces_root}/
└── {project-slug}/
    └── {team}/
        └── {agent-slug}/
            └── [git repo files]
```

## Example

```
/data/workspaces/
└── roboco/
    ├── backend/
    │   ├── be-dev-1/    # be-dev-1's workspace
    │   ├── be-dev-2/    # be-dev-2's workspace
    │   ├── be-qa/       # be-qa's workspace
    │   ├── be-pm/       # be-pm's workspace
    │   └── be-doc/      # be-doc's workspace
    ├── frontend/
    │   ├── fe-dev-1/
    │   └── ...
    └── ux_ui/
        └── ...
```

## Configuration

```bash
# Environment variables
ROBOCO_WORKSPACES_ROOT=/data/workspaces
ROBOCO_WORKSPACE_AUTO_CLONE=true
ROBOCO_WORKSPACE_CLONE_TIMEOUT=300
```

## Features

| Feature | Description |
|---------|-------------|
| Auto-clone | Workspaces created on first access |
| Isolation | No file locking conflicts |
| Branch independence | Agents on different branches |
| Project-scoped | Organized by project slug |

## Benefits

1. **Parallel Development**: Multiple agents on same project
2. **No Conflicts**: Each has own working tree
3. **Branch Flexibility**: Different branches simultaneously
4. **Clean State**: Fresh clone if needed

## Workspace Resolution

When agent needs workspace:

```python
# Service resolves path
workspace_path = await workspace_service.get_workspace(
    project_slug="roboco",
    agent_id="be-dev-1"
)
# Returns: /data/workspaces/roboco/backend/be-dev-1
```

If `auto_clone=True` and workspace doesn't exist, it's created automatically.
