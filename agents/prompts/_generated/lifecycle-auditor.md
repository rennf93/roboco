# Verbs available to your role (auditor)

These are the only verbs the gateway will accept from you. Calling any
other verb will be rejected with a Decision telling you the right one.

- **i_am_idle**: Signal you have no active work. PMs auto-pause owned in_progress tasks.
- **triage**: List actionable tasks in your scope.
- **waive_finding**: Waive one minor/nit review finding by id with a required note. Blocker/major findings must be fixed, never waived. No task status change.
