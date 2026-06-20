# Verbs available to your role (pr_reviewer)

These are the only verbs the gateway will accept from you. Calling any
other verb will be rejected with a Decision telling you the right one.

- **claim_gate_review**: Claim an assembled-PR review task (awaiting_pr_review) WITHOUT transitioning it — mirrors QA's claim_review. The assembled diff and the parent task's acceptance criteria are returned inline.
- **claim_pr_review**: Claim an inbound external-PR review task and start work. pending -> claimed -> in_progress.
- **give_me_work**: Return your most-actionable task or signal idle.
- **i_am_idle**: Signal you have no active work. PMs auto-pause owned in_progress tasks.
- **post_pr_review**: Post one complete change-request to the external PR and finish the review task. in_progress -> completed.
- **pr_fail**: Fail the assembled-PR review with concrete issues. Transitions awaiting_pr_review -> needs_revision, routed back like a QA fail.
- **pr_pass**: Pass the assembled-PR review. Transitions awaiting_pr_review -> awaiting_pm_review so the PM can merge.
