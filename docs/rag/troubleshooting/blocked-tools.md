# Blocked Tools

## Native Git Commands Blocked

**Symptom:** `Bash(git commit)`, `Bash(git push)`, `Bash(git checkout)`, etc. denied by the bash-guard hook.

**Cause:** Shell git for network / auth / branch-mutating ops bypasses the PAT injection done by the MCP layer; raw `git fetch` etc. would fail with `could not read Username for 'https://github.com'` anyway.

**Solution:** Use the role-scoped MCP verb that matches what you're trying to do. There is **no** `roboco_git_commit / _push / _create_pr / _merge_pr / _checkout` MCP tool — the surface is smaller than that:

| Blocked shell command | Use instead |
|-----------------------|-------------|
| `git status` / `git diff` / `git log` / `git branch` | `roboco_git_status` / `roboco_git_diff` / `roboco_git_log` / `roboco_git_branch_list` (roboco-git-readonly) |
| `git commit` + `git push` (devs / docs) | `commit(message, files)` (roboco-do) — auto-prefixes [task-id], pushes |
| `git checkout` of a task branch | None — branch is auto-checked-out by `i_will_work_on(task_id)` (devs) or `i_will_plan(task_id, plan)` (PMs) |
| Open a PR | None — PR is opened by the choreographer when the dev calls `open_pr(task_id)` |
| Merge a PR | `complete(task_id, notes)` (PMs only) — Cell PM merges leaf PR; Main PM merges parent and escalates to CEO |
| `git fetch` / `git pull` / `git rebase` | Devs have `sync_branch(task_id)` — the gate-level rebase (fetch + rebase + force-with-lease push). If your branch is **behind its base**, call `sync_branch(task_id)`; do NOT improvise shell git. PMs have no rebase verb — for a cell/root integration branch behind its base, `escalate_up(...)`. Use `unclaim` + re-`claim` only to rebuild a branch fresh from the current base, and only on instruction |

## Write/Edit Outside Workspace

**Symptom:** `Write()` or `Edit()` denied for a file path

**Cause:** Write operations restricted to your workspace

**Solution:**

- Developers: Only write in `/data/workspaces/{project}/{team}/{agent-id}/`
- Documenters: Only write in `/app/docs/`
- QA: No write access (review only)

## QA Cannot Commit

**Symptom:** `commit()` returns `not_authorized` for a QA agent

**Cause:** QA role is read-only — cannot modify code or open PRs.

**Solution:** QA `pass(task_id, notes)` or `fail(task_id, issues)` only. Developers fix issues and re-submit.

## NO_PLAN Error on Start

**Symptom:** Lifecycle transition rejected with NO_PLAN

**Cause:** Parent tasks require a plan before they can leave `pending`.

**Solution:** PMs call `i_will_plan(task_id, plan)`; the verb both records the plan and transitions the task into `in_progress`.

## Parent Branch Required

**Symptom:** Can't claim subtask, error "Parent task must be claimed first"

**Cause:** Parent task hasn't been claimed/started yet, so it has no branch for the subtask's branch to fork from.

**Solution:**

1. Parent task must transition to `in_progress` first (PMs: `i_will_plan(parent_id, plan)`; devs: `i_will_work_on(parent_id)`).
2. Then the subtask's branch will auto-fork from the parent's on claim.

Branches are auto-created hierarchically. No manual creation needed.
