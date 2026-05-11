# Verbs available to your role (developer)

These are the only verbs the gateway will accept from you. Calling any
other verb will be rejected with a Decision telling you the right one.

- **give_me_work**: Return your most-actionable task or signal idle.
- **i_am_blocked**: Escalate to PM. Logs a struggle journal entry.
- **i_am_done**: Submit work for QA. Auto-runs in_progress->verifying then verifying->awaiting_qa. Strict - PR must be open (call open_pr first) and >=1 commit.
- **i_am_idle**: Signal you have no active work. PMs auto-pause owned in_progress tasks.
- **i_will_work_on**: Claim a task, set the plan, and transition to in_progress. Atomic - preconditions checked before any state mutation.
- **open_pr**: Push the branch and open a PR. Atomic - preconditions (assignee, >=1 commit, no prior PR) checked BEFORE any git operation. After success, call i_am_done.
- **resume**: Resume a paused task you own. paused -> in_progress.
- **unclaim**: Voluntarily release a claim back to pending. The work-in-progress branch is preserved.
