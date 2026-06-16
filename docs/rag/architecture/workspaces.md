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

## No Workspace Tools — It's Automatic

There are **no** agent-facing workspace tools. Workspaces are created and cloned for you by the orchestrator (`WorkspaceService`) before your container starts. You never `ensure`, `clone`, or `checkout` a workspace by hand — your repo is already on disk at the path below, and the gateway verbs (`i_will_work_on`, `claim_review`, ...) check out the right branch.

## Workspace Resolution

Path resolved automatically: `{workspaces_root}/{project}/{team}/{agent}/`

If `auto_clone=True` and workspace doesn't exist, it's created on first access.

## Authentication

HTTPS repositories require a GitHub PAT configured on the project:

- **Token configured**: Auto-clone works, git operations succeed
- **Token missing**: Error "Project requires a git token for HTTPS repositories"

**If you see this error**: Contact your PM. The project's git token is configured by a human in the control panel (project settings) — it is not an agent tool. The token is encrypted at rest and never exposed to your container; the orchestrator injects it into git operations for you.
