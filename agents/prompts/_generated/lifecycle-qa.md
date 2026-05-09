# Verbs available to your role (qa)

These are the only verbs the gateway will accept from you. Calling any
other verb will be rejected with a Decision telling you the right one.

- **claim_review**: Claim a task in awaiting_qa for review. Returns evidence inline.
- **fail_review**: Fail QA with concrete issues. Transitions to needs_revision.
- **give_me_work**: Return your most-actionable task or signal idle.
- **i_am_blocked**: Escalate to PM. Logs a struggle journal entry.
- **i_am_idle**: Signal you have no active work. PMs auto-pause owned in_progress tasks.
- **pass_review**: Pass QA. Transitions awaiting_qa -> awaiting_documentation.
- **resume**: Resume a paused task you own. paused -> in_progress.
- **unclaim**: Voluntarily release a claim back to pending. The work-in-progress branch is preserved.
