# Spawn-time base-clone refresh for non-author roles

Extends the #692 spawn-time self-heal family. #692 refreshes per-task
worktrees at spawn; this change does the same for the **base clone's own
checkout**, for roles that never author.

## Why

A reader clone's checkout can sit far behind origin (a live one was ~19 wall
hours behind `origin/slave`, mostly fleet downtime). Raw file reads then
disagreed with the origin-preferring git-log reader (#690) about the same
branch. Every QA / reviewer / board decision is only as fresh as the clone it
reads.

## Where it runs

- `SpawnLaunchEngine._maybe_refresh_base_clone` (`roboco/runtime/engines/spawn_launch.py`)
  is called at the top of `_ensure_worktree_before_spawn`, i.e. the same
  chokepoint as the per-task worktree heal, **before** the branchless early
  return. It therefore runs on every spawn, including branchless
  coordination/board spawns, which are pure readers.
- It delegates to `WorkspaceService.refresh_base_clone_checkout(clone_root, project_slug)`
  (`roboco/services/workspace.py`), next to `_refresh_present_worktree`. There
  is no parallel refresh path.

## Role classification (capability-based, conservative)

"Non-author" is defined by capability: roles that hold the gateway `commit`
verb are authors and are self-maintaining
(`foundation.identity.WORKTREE_AUTHOR_ROLES`, checked via
`is_worktree_author_role`).

| Role resolves to | Behavior |
|---|---|
| Author role (developer, documenter, ...) | Untouched. The #692 dirty-edit guarantee (never lose uncommitted edits) holds exactly as before. |
| `unknown` (unresolvable slug) | Treated as author: refresh skipped, skip logged. |
| Any other (reader) role, e.g. QA | Refreshed as below. |

When in doubt, the code treats the agent as an author.

## Refresh policy

1. **Fetch first.** Origin refs of a stale clone cannot be assumed current, so
   the fetch (`_fetch_branch_ref`) happens before any behind-vs-diverged
   decision.
2. Read the checked-out branch and its `HEAD` (the "before" tip), then compare
   against `origin/<branch>` with `rev-list --count` in both directions.

| Checkout vs `origin/<branch>` | Action |
|---|---|
| Strictly behind | `reset --hard origin/<branch>` (exactly a fast-forward). Logged with `was="behind"`, `before_tip`, `after_tip`. |
| Diverged | `reset --hard origin/<branch>`. Readers never author, so no legitimate local edits exist to lose. Logged with `was="diverged"`, before/after tips. |
| In sync | No-op. |
| Strictly ahead | Skipped and logged (never rewrite local refs). |
| Detached / unknown HEAD | Skipped and logged. |
| Fetch failed, or `origin/<branch>` unresolvable | Skipped. |

## Failure handling

Best-effort, mirroring the worktree heal: any exception in the refresh is
logged as `base-clone refresh failed (best-effort)` and the spawn continues.
A refresh never breaks a spawn.

## Tests

- `tests/unit/services/test_workspace_base_clone_refresh.py` — real git against
  a real bare origin: behind (origin content readable on first file read),
  diverged, in-sync, strictly-ahead, detached HEAD.
- `tests/unit/runtime/test_spawn_worktree_ensure.py` — refresh runs for reader
  roles only (be-qa yes, be-dev-1 no), unknown role skipped, refresh failure
  never breaks a spawn.
