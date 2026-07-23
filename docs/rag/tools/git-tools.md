# Git Tools

There is **no** "roboco_git_commit / _push / _create_pr / _merge_pr / _checkout" MCP tool. Anything mutating the repo goes through one of two role-scoped verbs and the choreographer handles git for you.

## Read Operations (any role) — `roboco-git-readonly`

| Tool | Purpose |
|------|---------|
| `roboco_git_status` | View working tree status |
| `roboco_git_log` | View commit history |
| `roboco_git_branch_list` | List branches |
| `roboco_git_diff` | View changes |

```python
# project_slug is optional — omit it and your own project is used
# (from this agent's environment). Pass it explicitly only to inspect
# a different project than the one you're assigned to.
status = roboco_git_status()
diff = roboco_git_diff()
log = roboco_git_log(branch="feature/backend/a1b2c3d4")
branches = roboco_git_branch_list()
```

## Branch Lifecycle — automatic

Branches are auto-created when an agent transitions a task to `in_progress`:

- Root task → `feature/team/ROOT_ID`
- Subtask → `feature/team/ROOT_ID--SUB_ID`
- Sub-subtask → `feature/team/ROOT_ID--SUB_ID--SUBSUB_ID`

You never run `git checkout` or `git branch` yourself; calling `i_will_work_on(task_id)` (developers) or `i_will_plan(task_id, plan)` (PMs) creates the branch and switches your workspace to it.

## Write Path — by role

### Developers and Documenters → `commit` (roboco-do)

```python
# Commit on your active task's branch. The choreographer:
#  - prefixes the message with [task-id]
#  - validates against commit_validator
#  - pushes to the remote branch
#  - opens a PR when the task transitions out of in_progress
commit(message="Add rate limiting endpoint", files=["roboco/api/routes/rate.py"])
```

There is no separate `push` step and no separate `create_pr` step. Both are side-effects of the lifecycle transitions the verbs already drive.

On a project that checks in generated artifacts, the push step also regenerates them: right before your branch is pushed, the project's codegen command runs in your worktree, and any drift it produces is committed into the SAME push automatically — you don't need to run it yourself, and a broken/timing-out codegen command never blocks your push (it just logs and lets you proceed; a real drift then surfaces on CI's own drift gate instead).

### PMs → `complete` (roboco-flow)

```python
# Cell PM completing a leaf task: merges the leaf PR.
# (Assembled cell→root / root→master PRs are opened by submit_up / submit_root
#  and gated in awaiting_pr_review first.) After a code root's gate clears,
# the Main PM's complete escalates to the CEO — the CEO merges root→master.
complete(task_id="a1b2c3d4-...", notes="QA passed; docs complete; ready to ship.")
```

PMs never run `git` directly and have no commit/push tools. PMs `delegate` code work to devs, then `complete` to merge once QA + docs sign off.

## Branch Naming Convention

`{type}/{team}/{task-hierarchy}`

| Type | Use |
|------|-----|
| `feature/` | New functionality |
| `bug/` | Bug fixes |
| `chore/` | Maintenance |
| `docs/` | Documentation |
| `hotfix/` | Urgent fixes |

Hierarchy uses `--` (two hyphens) as the separator, not `/`, so a hierarchy slug like `ABC12345--DEF67890--GHI11111` is one git branch segment, not three nested directories.
