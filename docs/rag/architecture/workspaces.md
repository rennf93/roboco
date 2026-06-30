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

## Per-Task Worktrees (F123)

Your agent clone is **shared across all your tasks**, but each **claimed task** gets its own linked git worktree — a separate working directory on the same underlying clone — so two of your in-progress tasks never clobber each other on one checkout (this is what lets a coordinator PM hold several roots at once, and what stops a fresh claim from `reset --hard`-ing uncommitted work on your still-active first task).

```
{workspaces_root}/{project}/{team}/{agent}/        # the clone root (shared)
├── .git/                  # the real object store (shared)
├── .venv/                 # the per-project venv (shared, agent-owned)
├── .uv-python/            # uv's managed CPython (shared, gitignored)
└── .worktrees/
    └── {task-short}/      # ONE per claimed task — your cwd for that task
        ├── .venv -> ../../.venv   # symlink to the clone-root venv
        └── [your task's checked-out branch]
```

- **Claim** (`i_will_work_on` / `claim_review` / `claim_doc_task`) adds a worktree at `.worktrees/{task-id-first-8}/` and checks out the task's branch there. The clone root's HEAD is **never moved** by a claim.
- **Your container is started with `-w` pointing at the worktree** for your current task, so `commit`, edits, and `uv run` all resolve there automatically. Spawn resolves the worktree from your `current_task_id` on every spawn (never cached), so a resume/respawn re-attaches a pruned worktree before launch.
- **The clone-root `.venv` is shared** — each worktree's `.venv` is a symlink to `../../.venv`, so `uv run` from a worktree resolves the clone-root venv. No per-worktree re-sync.
- **Git ops split by kind**: checkout/HEAD-moving ops (`create_branch`, `commit`, `rebase`, `checkout`) target the worktree; branch-by-name ops (`push`, `pull`, `fetch`, `pr_merge`, `diff`) run from the clone root. You never do either by hand — the verbs resolve the worktree for you.
- **One active WorkSession per task** is enforced both in the service layer and by a DB unique index — a re-claim (pool release, reaper unclaim, escalation redirect) supersedes any prior agent's stale session for that task.
- **Claim rollback** (a mid-claim failure) `worktree remove --force`s the worktree so a retry doesn't collide with a stale one.
- **Terminal completion** (`complete` / `ceo_approve`) removes the assignee's worktree best-effort, so finished tasks don't accumulate. A `needs_revision` bounce keeps the worktree — you need it back. The stale-claim reaper does **not** remove the worktree; it routes the task to `pending` for a re-claim that reuses it.

You do not manage any of this. The verbs do. The only thing you must know: **your cwd is the worktree for your current task, not the clone root** — so relative paths and `uv run` resolve against your task's checkout.

## The `/app/.venv` is sacred — never retarget onto it

Two venv classes exist in the container:

- **Workspace venvs** — per-project, agent-owned, under `/data/workspaces/.../{agent}/.venv`. These are yours.
- **`/app/.venv`** — the image-baked MCP-gateway venv. The MCP servers (`roboco-flow`, `roboco-do`, the git-readonly server) import from here. **It is sacred. If it breaks, every tool you have stops spawning.**

A past live incident: an agent hit a permission error on its workspace venv, followed uv's hint to run `uv run --active`, and that retargeted onto `VIRTUAL_ENV=/app/.venv` (baked globally) — uv rebuilt `/app/.venv` from a drifted lock and deleted its `bin/`, bricking every MCP server spawn fleet-wide.

The bash-guard hook now **blocks** `uv run --active` and any `uv run` / `uvx` against `/app` (`--project /app`, `--directory /app`, `UV_PROJECT_ENVIRONMENT=/app`, `cd /app && uv ...`). If you ever feel tempted to use `--active` or point uv at `/app`, **don't** — call `i_am_blocked(reason='workspace venv broken')` instead and let the environment be rebuilt. Bare `uv run` (cwd-relative, your workspace venv) is always fine and always what you want.

## No Workspace Tools — It's Automatic

There are **no** agent-facing workspace tools. Workspaces and per-task worktrees are created for you by the orchestrator (`WorkspaceService`) before your container starts. You never `ensure`, `clone`, `checkout`, or `worktree add` by hand — your repo is already on disk, the worktree for your current task is already linked and `-w`'d as your cwd, and the gateway verbs (`i_will_work_on`, `claim_review`, ...) check out the right branch in it.

## Workspace Resolution

Path resolved automatically: `{workspaces_root}/{project}/{team}/{agent}/`

If `auto_clone=True` and workspace doesn't exist, it's created on first access.

## Authentication

HTTPS repositories require a GitHub PAT configured on the project:

- **Token configured**: Auto-clone works, git operations succeed
- **Token missing**: Error "Project requires a git token for HTTPS repositories"

**If you see this error**: Contact your PM. The project's git token is configured by a human in the control panel (project settings) — it is not an agent tool. The token is encrypted at rest and never exposed to your container; the orchestrator injects it into git operations for you.
