# Git Error Troubleshooting

## Missing Git Token

**Error:** `Project requires a git token for HTTPS repositories` (also surfaces as `WorkspaceError` during clone)

**Cause:** No encrypted GitHub PAT on `projects.git_token_encrypted` for this project.

**Fix:**

1. Open the project's settings tab in the panel
2. Paste a GitHub Personal Access Token with `repo` scope
3. Save — the panel encrypts and stores it; the API never returns the plaintext

Notes:

- Each project has its own token (no global fallback)
- Tokens are encrypted at rest with Fernet
- The token is injected only at the MCP layer (commit / clone / PR ops); `.git/config` is scrubbed post-clone so a leaked PAT from there is not a recovery path

## Workspace Not Found

**Error:** `Workspace does not exist`

**Cause:** Workspace not cloned yet (or `ROBOCO_WORKSPACE_AUTO_CLONE` is `false` and no manual clone has run).

**Fix:**

- If `ROBOCO_WORKSPACE_AUTO_CLONE=true` (default), the first MCP verb that touches the workspace will trigger the clone. Just call your next verb (`i_will_work_on`, `commit`, etc.).
- Otherwise check `ROBOCO_WORKSPACE_CLONE_TIMEOUT` and the orchestrator logs for a stuck clone.

## BRANCH_MISMATCH

**Error envelope:** `Workspace is on '<other-branch>' but task requires '<task-branch>'`

**Cause:** You're trying to act on task A while your workspace is still on task B's branch — usually after a respawn/resume on a shared clone, or a re-provisioned clone that no longer has your task's branch locally.

**Auto-recovery first:** a commit-time check now *recovers* this for you — it re-checks out your task's branch (recreating a missing local ref from origin) before acting, without ever discarding your local commits. So you normally won't see this error on resume at all; just continue your work.

**When you still see it:** the only case left is **uncommitted changes blocking the switch** (the clone can't move off its current branch). Then:

- `commit(message=..., files=...)` your in-progress work, or escalate via `i_am_blocked` if the changes aren't yours / are conflicting.
- Re-call your role's claim verb on the intended task: `i_will_work_on(task_id)` (devs), `i_will_plan(task_id, plan)` (PMs), `claim_doc_task(task_id)` (documenters), `claim_review(task_id)` (QA).

Don't checkout by hand — there is no `roboco_git_checkout` tool.

## NO_COMMITS on open_pr

**Cause:** No commits on the task yet — the choreographer has nothing to open a PR over.

**Fix:** `commit(message=..., files=...)` at least once, then call `open_pr(task_id)` again.

## Branch Behind Its Base / master

**Symptom:** `roboco_git_status` shows `behind > 0` against the base branch when you go to submit.

**Cause:** The base (cell branch or master) advanced after your branch was cut, so your branch is stale.

**Fix:** You have no rebase verb — do **not** create a rebase task or improvise with shell git. Bringing the branch current is a platform/PM action. Escalate: devs `i_am_blocked(reason="branch behind base — needs rebase")`; PMs `escalate_up(...)`.

## src refspec does not match any (during open_pr)

**Error:** `src refspec '<branch>' does not match any`

**Cause:** A re-provisioned or shared clone can be missing the local branch ref even though the commits are already on origin.

**Fix:** Just call `open_pr(task_id)` again — the gateway recovers the ref from origin, or returns an explicit "unclaim and re-claim to rebuild" instruction. Follow whichever the envelope gives you; don't switch branches by hand.

## Updates were rejected / non-fast-forward (on push)

**Cause:** Your branch is behind its remote, so the push can't fast-forward.

**Fix:** Escalate rather than improvise — devs `i_am_blocked(...)`, PMs `escalate_up(...)`. There is no agent-layer pull/rebase to reconcile it.

## NO_PR on pass / fail

**Cause:** The PR was never created — usually because `open_pr(task_id)` did not run cleanly.

**Fix:** Roll back to the dev: have them re-call `open_pr(task_id)` after fixing whatever blocked the PR opening (see PR Creation Failed, below). QA cannot create the PR.

## PR Creation Failed (during open_pr)

**Causes:**

1. Nothing to push — no commits on the branch
2. Branch is on the workspace but not pushed yet (rare; the choreographer pushes during `commit`, but a stale workspace can drift)
3. Project has no git token configured
4. The GitHub repo doesn't allow PRs from your branch (rare; usually org-level branch protection)

**Fix:**

- Verify commits exist with `roboco_git_log(project_slug=...)`
- Verify the project has a git token (Missing Git Token, above)
- If the task is in a stuck state, `unclaim(task_id)` and re-`claim` to rebuild the branch

## FORCE_PUSH_FORBIDDEN

**Cause:** Force-push is CEO-only. Anyone else attempting it (typically because their branch diverged) is denied.

**Fix:** `unclaim(task_id)` and re-`claim` it. The choreographer rebuilds the branch from the parent's HEAD; replay your commits with `commit(...)`.

## Merge Conflicts on `complete`

**Cause:** The leaf PR conflicts with the parent branch (cell branch or master).

**Fix:** This currently surfaces as an error envelope from `complete`. The recovery path:

1. PM `unblock(task_id, restore=False)` — frees the task back to the dev
2. Dev re-claims, the choreographer rebuilds the branch off the latest parent, and they replay their commits
3. Dev `open_pr` again
4. QA re-runs `pass` (or `fail` if the rebase changed behaviour)
5. PM `complete` again

We don't expose a "resolve conflicts in place" path at the agent layer — rebuilds via the lifecycle are the recovery.
