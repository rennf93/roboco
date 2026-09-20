# Verbs available to your role (devops)

These are the only verbs the gateway will accept from you. Calling any
other verb will be rejected with a Decision telling you the right one.

- **claim_gate_review**: Claim an assembled-PR review task (awaiting_pr_review) WITHOUT transitioning it — mirrors QA's claim_review. The assembled diff and the parent task's acceptance criteria are returned inline.
- **give_me_work**: Return your most-actionable task or signal idle.
- **i_am_done**: Submit work for QA. Auto-runs in_progress->verifying then verifying->awaiting_qa. Strict - PR must be open (call open_pr first) and >=1 commit.
- **i_am_idle**: Signal you have no active work. PMs auto-pause owned in_progress tasks.
- **i_will_work_on**: Claim a task, set the plan, and transition to in_progress. Atomic - preconditions checked before any state mutation.
- **open_pr**: Push the branch and open a PR. Atomic - preconditions (assignee, >=1 commit, no prior PR) checked BEFORE any git operation. After success, call i_am_done.
- **pr_fail**: Fail the assembled-PR review with concrete issues. Transitions awaiting_pr_review -> needs_revision, routed back like a QA fail.
- **pr_pass**: Pass the assembled-PR review. Transitions awaiting_pr_review -> awaiting_pm_review so the PM can merge.
- **sync_branch**: Rebase your task's branch onto its current base THROUGH the gate (raw git is denied). Use when your branch has fallen behind its base — e.g. a sibling task's PR merged into the parent branch while you worked. Fetches origin, rebases head onto base, and force-pushes (with-lease). No DB state change. On conflicts the rebase is aborted and the conflicted files are returned — resolve by hand, commit, then sync_branch again. Pass stash=True to auto-stash uncommitted changes instead of refusing DIRTY_WORKSPACE; they are restored after the rebase.
- **unclaim**: Voluntarily release a claim back to pending. The work-in-progress branch is preserved. A PR reviewer who claimed an external review (in_progress) or a gate review (awaiting_pr_review) and cannot finish releases the claim here rather than wedging the lane until the stale-claim reaper.
