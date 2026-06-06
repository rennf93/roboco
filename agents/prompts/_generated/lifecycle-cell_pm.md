# Verbs available to your role (cell_pm)

These are the only verbs the gateway will accept from you. Calling any
other verb will be rejected with a Decision telling you the right one.

- **complete**: Cell PM merges leaf PR + transitions to completed; Main PM merges root PR + escalates to CEO.
- **delegate**: Create a subtask under the current task. Validates the delegation chain (main_pm->cell_pm; cell_pm->its team's devs) and the assignee-vs-task_type rule (Cell PMs get planning-typed tasks; devs get code/research, UX devs also design). documentation is NOT delegatable — the lifecycle auto-creates the doc phase after the code subtask passes QA.
- **escalate_up**: Escalate to your role's escalation_target.
- **give_me_work**: Return your most-actionable task or signal idle.
- **i_am_idle**: Signal you have no active work. PMs auto-pause owned in_progress tasks.
- **i_will_plan**: PM mirror of i_will_work_on for parent tasks. Claim, plan, transition to in_progress; from there delegate subtasks.
- **reassign**: Hand a claimed/in_progress task to another developer in your own cell. The branch is keyed to the task (not the agent), so it is preserved — the new developer continues the work-in-progress. No status change.
- **resume**: Resume a paused task you own. paused -> in_progress.
- **submit_up**: Cell PM opens the cell→root PR and moves the cell task to awaiting_pm_review. The same Cell PM then completes it.
- **triage**: List actionable tasks in your scope.
- **unblock**: PM unblocks a blocked task; restores pre-block state.
- **unclaim**: Voluntarily release a claim back to pending. The work-in-progress branch is preserved.
