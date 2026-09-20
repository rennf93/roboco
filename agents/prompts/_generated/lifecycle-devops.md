# Verbs available to your role (devops)

These are the only verbs the gateway will accept from you. Calling any
other verb will be rejected with a Decision telling you the right one.

- **claim_gate_review**: Claim an assembled-PR review task (awaiting_pr_review) WITHOUT transitioning it — mirrors QA's claim_review. The assembled diff and the parent task's acceptance criteria are returned inline.
- **give_me_work**: Return your most-actionable task or signal idle.
- **i_am_done**: Submit work for QA. Auto-runs in_progress->verifying then verifying->awaiting_qa. Strict - PR must be open (call open_pr first) and >=1 commit.
- **i_am_idle**: Signal you have no active work. PMs auto-pause owned in_progress tasks.
- **pr_fail**: Fail the assembled-PR review with concrete issues. Transitions awaiting_pr_review -> needs_revision, routed back like a QA fail.
- **pr_pass**: Pass the assembled-PR review. Transitions awaiting_pr_review -> awaiting_pm_review so the PM can merge.
- **unclaim**: Voluntarily release a claim back to pending. The work-in-progress branch is preserved. A PR reviewer who claimed an external review (in_progress) or a gate review (awaiting_pr_review) and cannot finish releases the claim here rather than wedging the lane until the stale-claim reaper.
