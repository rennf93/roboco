# Multi-Agent Workspace Architecture

This document describes the workspace structure that enables multiple AI agents to work on the same project in parallel without conflicts.

## Overview

Each agent gets their own git clone (workspace) of a project. This allows:

- **Parallel development**: Multiple agents working on different tasks simultaneously
- **No file conflicts**: Each agent has their own working tree
- **Independent branches**: Agents can be on different branches
- **Scoped permissions**: Agents only have access to their own workspace

## Directory Structure

```
{workspaces_root}/
└── {project-slug}/
    └── {team}/
        └── {agent-slug}/
            └── [git repository files]
```

### Example

```
/data/workspaces/
├── roboco/                           # Project: roboco
│   ├── backend/                      # Team: backend
│   │   ├── be-dev-1/                 # Agent: be-dev-1
│   │   │   ├── .git/
│   │   │   ├── roboco/
│   │   │   └── ...
│   │   └── be-dev-2/                 # Agent: be-dev-2
│   │       ├── .git/
│   │       ├── roboco/
│   │       └── ...
│   ├── frontend/                     # Team: frontend
│   │   ├── fe-dev-1/
│   │   └── fe-dev-2/
│   └── uxui/                         # Team: uxui
│       └── ux-dev-1/
│
└── roboco-panel/                     # Project: roboco-panel
    ├── frontend/
    │   ├── fe-dev-1/
    │   └── fe-dev-2/
    └── ...
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_WORKSPACES_ROOT` | `/data/workspaces` | Root directory for all workspaces |
| `ROBOCO_WORKSPACE_AUTO_CLONE` | `true` | Auto-clone repos on first access |
| `ROBOCO_WORKSPACE_CLONE_TIMEOUT` | `300` | Clone timeout in seconds |

### Example `.env`

```bash
ROBOCO_WORKSPACES_ROOT=/data/workspaces
ROBOCO_WORKSPACE_AUTO_CLONE=true
ROBOCO_WORKSPACE_CLONE_TIMEOUT=300
```

## How It Works

### 1. Workspace Resolution

When an agent makes a git/test API request:

```
Agent: be-dev-1 (team: backend)
Project: roboco
→ Workspace: /data/workspaces/roboco/backend/be-dev-1/
```

### 2. Auto-Clone

If `ROBOCO_WORKSPACE_AUTO_CLONE=true` and workspace doesn't exist:

1. Create parent directories
2. Clone from project's `git_url`
3. Checkout `default_branch`

### 3. API Flow

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  Agent      │────▶│  API Endpoint   │────▶│  WorkspaceService│
│  (be-dev-1) │     │  (git/test)     │     │                  │
└─────────────┘     └─────────────────┘     └──────────────────┘
                           │                         │
                           ▼                         ▼
                    ┌─────────────┐          ┌──────────────────┐
                    │ X-Agent-ID  │          │ Resolve path:    │
                    │ X-Agent-Role│          │ /workspaces/     │
                    │ X-Agent-Team│          │   roboco/        │
                    └─────────────┘          │   backend/       │
                                             │   be-dev-1/      │
                                             └──────────────────┘
```

## API Endpoints

### WorkspaceService Methods

```python
from roboco.services.workspace import get_workspace_service

service = get_workspace_service(db)

# Get workspace path
path = service.get_workspace_path("roboco", "backend", "be-dev-1")
# → Path("/data/workspaces/roboco/backend/be-dev-1")

# Resolve from agent UUID
path = await service.resolve_workspace("roboco", agent_uuid)

# Ensure workspace exists (clone if needed)
path = await service.ensure_workspace(
    project_slug="roboco",
    agent_id=agent_uuid,
    git_url="git@github.com:org/roboco.git",
    default_branch="main"
)

# List all workspaces for a project
workspaces = await service.list_workspaces("roboco")
# [{"team": "backend", "agent": "be-dev-1", "path": "...", "exists": True}, ...]

# Delete workspace (use with caution)
deleted = await service.delete_workspace("roboco", agent_uuid)
```

## Git Workflow

### Branch Naming

Each agent works on task-specific branches:

```
{type}/{team}/{task-id-first-8-chars}

Examples:
- feature/backend/abc12345   (be-dev-1 on Task ABC12345)
- fix/backend/def67890       (be-dev-2 on Task DEF67890)
- feature/frontend/ghi11223  (fe-dev-1 on Task GHI11223)
```

### Parallel Work Example

```
be-dev-1 workspace:
  └── branch: feature/backend/task-001
      └── Working on user authentication

be-dev-2 workspace:
  └── branch: fix/backend/task-002
      └── Fixing database connection issue

(Both agents work simultaneously, no conflicts)
```

## Backwards Compatibility

The system maintains backwards compatibility with the legacy `workspace_path` field on Projects:

1. If `agent_id` is provided → Use multi-agent workspace resolution
2. If `agent_id` is `None` → Fall back to `project.workspace_path`

This allows gradual migration from single-workspace to multi-agent workspaces.

## Best Practices

### For PMs

1. **Register projects** with `git_url` - workspaces are created automatically
2. **Don't set `workspace_path`** on projects - let the system manage workspaces
3. **Assign tasks to specific agents** - each gets their own workspace

### For Developers (Agents)

1. **Always work in your workspace** - don't access other agents' workspaces
2. **Commit frequently** - your workspace is yours alone
3. **Create PRs** - merge through the standard PR process

### For Operations

1. **Set `ROBOCO_WORKSPACES_ROOT`** to a location with sufficient disk space
2. **Consider NFS/shared storage** for multi-node deployments
3. **Monitor disk usage** - workspaces can grow large

## Troubleshooting

### Workspace Not Found

```
WorkspaceError: Agent not found: be-dev-1
```

**Solution**: Ensure the agent exists in the database with correct team.

### Clone Failed

```
WorkspaceError: Failed to clone repository: Permission denied
```

**Solution**: Ensure the RoboCo service has SSH keys configured for git access.

### Disk Space

```
WorkspaceError: No space left on device
```

**Solution**: Clean up old workspaces or expand storage:

```python
# Delete workspace for an agent
await service.delete_workspace("roboco", agent_uuid)
```

## Security Considerations

1. **Workspace Isolation**: Agents should only access their own workspaces
2. **Git Credentials**: Store SSH keys securely, don't expose in workspaces
3. **File Permissions**: Ensure appropriate Unix permissions on workspace directories
4. **Network Access**: Workspaces need network access for git operations
