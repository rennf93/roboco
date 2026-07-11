# Verbs available to your role (main_pm)

These are the only verbs the gateway will accept from you. Calling any other verb will be rejected with a Decision telling you the right one.

- **complete**: Cell PM merges the PR (leaf into the cell branch, or the gated cell→root PR into the root branch) + transitions to completed; Main PM escalates the root to the CEO (who merges root→master). The merge runs BEFORE the complete transition: TaskService.complete asserts the PR is already merged, so the choreographer verb body (cell_pm_complete / main_pm_complete) owns the merge-first ordering — no trailing pr_merge side_effect is declared here.
- **declare_coverage**: Stamp parent acceptance criteria onto an existing child's parent_ac_refs after the fact — for a replacement child whose delegate omitted covers_parent_criteria. Or, targeting your OWN root/coordination task, declare criteria as root-owned (only your own machinery satisfies them — never push these into a cell). No status change; the verb body owns ownership + criterion validation.
- **delegate**: Create a subtask under the current task. Validates the delegation chain (main_pm->cell_pm; cell_pm->its team's devs) and the assignee-vs-task_type rule (Cell PMs get planning-typed tasks; devs get code/research, UX devs also design). documentation is NOT delegatable — the lifecycle auto-creates the doc phase after the code subtask passes QA.
- **escalate_to_ceo**: Escalate to CEO with reason. Transitions to awaiting_ceo_approval.
- **escalate_up**: Escalate to your role's escalation_target.
- **give_me_work**: Return your most-actionable task or signal idle.
- **i_am_idle**: Signal you have no active work. PMs auto-pause owned in_progress tasks.
- **i_will_plan**: PM mirror of i_will_work_on for parent tasks. Claim, plan, transition to in_progress; from there delegate subtasks.
- **request_changes**: Reject the merge review with concrete issues. Transitions awaiting_pm_review -> needs_revision, routed back like a QA fail (original developer for a leaf, revision PM for an assembled task). Use this for an AC/scope violation caught at merge review — never i_am_blocked/escalate, which have no revision routing.
- **resume**: Resume a paused task you own. paused -> in_progress.
- **submit_root**: Main PM opens the root→master PR and moves the root task to awaiting_pr_review for the main reviewer (the root analogue of the cell PM's submit_up). After pr_pass, call complete to escalate to the CEO. For branch-bearing roots (a Main-PM root-subtask assembles the cells' merged work); branchless coordination roots skip the gate and complete directly. The gate is branch-keyed, not task_type-keyed — a Main-PM root is planning-typed, never code.
- **triage**: List actionable tasks in your scope.
- **triage_all**: List actionable tasks across all teams (Main PM only).
- **unblock**: PM unblocks a blocked task; restores pre-block state.
- **unclaim**: Voluntarily release a claim back to pending. The work-in-progress branch is preserved. A PR reviewer who claimed an external review (in_progress) or a gate review (awaiting_pr_review) and cannot finish releases the claim here rather than wedging the lane until the stale-claim reaper.
