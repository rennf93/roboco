# Verbs available to your role (head_marketing)

These are the only verbs the gateway will accept from you. Calling any
other verb will be rejected with a Decision telling you the right one.

- **escalate_to_ceo**: Escalate to CEO with reason. Transitions to awaiting_ceo_approval.
- **i_am_idle**: Signal you have no active work. PMs auto-pause owned in_progress tasks.
- **triage**: List actionable tasks in your scope.
