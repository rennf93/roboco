# Verbs available to your role (cell_pm)

These are the only verbs the gateway will accept from you. Calling any
other verb will be rejected with a Decision telling you the right one.

- **complete**: Cell PM merges leaf PR + transitions to completed; Main PM merges root PR + escalates to CEO.
- **delegate**: Create a subtask under the current task. Validates the delegation chain (main_pm->cell_pm; cell_pm->its team's devs) and the assignee-vs-task_type rule (Cell PMs get planning-typed tasks; devs get code/documentation).
- **escalate_up**: Escalate to your role's escalation_target.
- **give_me_work**: Return your most-actionable task or signal idle.
- **i_am_idle**: Signal you have no active work. PMs auto-pause owned in_progress tasks.
- **i_will_plan**: PM mirror of i_will_work_on for parent tasks. Claim, plan, transition to in_progress; from there delegate subtasks.
- **resume**: Resume a paused task you own. paused -> in_progress.
- **submit_up**: Cell PM bubbles a finished cell-scope task up to Main PM.
- **triage**: List actionable tasks in your scope.
- **unblock**: PM unblocks a blocked task; restores pre-block state.
- **unclaim**: Voluntarily release a claim back to pending. The work-in-progress branch is preserved.
