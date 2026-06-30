# Intent Verbs (gateway-facing surface)

## claim_doc_task

Claim awaiting_documentation. Returns evidence inline.

**Allowed roles:** documenter

**Composes:** (no atomic actions)


## claim_gate_review

Claim an assembled-PR review task (awaiting_pr_review) WITHOUT transitioning it — mirrors QA's claim_review. The assembled diff and the parent task's acceptance criteria are returned inline.

**Allowed roles:** pr_reviewer

**Composes:** (no atomic actions)


## claim_pr_review

Claim an inbound external-PR review task and start work. pending -> claimed -> in_progress.

**Allowed roles:** pr_reviewer

**Composes:** claim → start


## claim_review

Claim a task in awaiting_qa for review. Returns evidence inline.

**Allowed roles:** qa

**Composes:** (no atomic actions)


## complete

Cell PM merges the PR (leaf into the cell branch, or the gated cell→root PR into the root branch) + transitions to completed; Main PM escalates the root to the CEO (who merges root→master).

**Allowed roles:** cell_pm, main_pm

**Composes:** complete

**Side effects:** pr_merge


## delegate

Create a subtask under the current task. Validates the delegation chain (main_pm->cell_pm; cell_pm->its team's devs) and the assignee-vs-task_type rule (Cell PMs get planning-typed tasks; devs get code/research, UX devs also design). documentation is NOT delegatable — the lifecycle auto-creates the doc phase after the code subtask passes QA.

**Allowed roles:** cell_pm, main_pm

**Composes:** create_subtask


## escalate_to_ceo

Escalate to CEO with reason. Transitions to awaiting_ceo_approval.

**Allowed roles:** head_marketing, main_pm, product_owner

**Composes:** escalate_to_ceo


## escalate_up

Escalate to your role's escalation_target.

**Allowed roles:** cell_pm, main_pm

**Composes:** (no atomic actions)

**Preconditions:** non_terminal


## fail_review

Fail QA with concrete issues. Transitions to needs_revision.

**Allowed roles:** qa

**Composes:** qa_fail


## give_me_work

Return your most-actionable task or signal idle.

**Allowed roles:** cell_pm, developer, documenter, main_pm, pr_reviewer, qa

**Composes:** (no atomic actions)


## i_am_blocked

Escalate to PM. Logs a struggle journal entry.

**Allowed roles:** developer, documenter, qa

**Composes:** block


## i_am_done

Submit work for QA. Auto-runs in_progress->verifying then verifying->awaiting_qa. Strict - PR must be open (call open_pr first) and >=1 commit.

**Allowed roles:** developer

**Composes:** submit_verification → submit_qa

**Preconditions:** commits>=1, owns_task


## i_am_idle

Signal you have no active work. PMs auto-pause owned in_progress tasks.

**Allowed roles:** auditor, cell_pm, developer, documenter, head_marketing, main_pm, pr_reviewer, product_owner, prompter, qa, secretary

**Composes:** (no atomic actions)


## i_documented

Signal docs complete. Transitions to awaiting_pm_review.

**Allowed roles:** documenter

**Composes:** docs_complete


## i_will_plan

PM mirror of i_will_work_on for parent tasks. Claim, plan, transition to in_progress; from there delegate subtasks.

**Allowed roles:** cell_pm, main_pm

**Composes:** claim → set_plan → start

**Preconditions:** plan


## i_will_work_on

Claim a task, set the plan, and transition to in_progress. Atomic - preconditions checked before any state mutation.

**Allowed roles:** developer

**Composes:** claim → set_plan → start

**Preconditions:** plan


## open_pr

Push the branch and open a PR. Atomic - preconditions (assignee, >=1 commit, no prior PR) checked BEFORE any git operation. After success, call i_am_done.

**Allowed roles:** developer

**Composes:** (no atomic actions)

**Side effects:** push_branch, create_pr

**Preconditions:** commits>=1, no_prior_pr, owns_task, pr_open_state


## pass_review

Pass QA. Transitions awaiting_qa -> awaiting_documentation.

**Allowed roles:** qa

**Composes:** qa_pass


## post_pr_review

Post one complete change-request to the external PR and finish the review task. in_progress -> completed.

**Allowed roles:** pr_reviewer

**Composes:** pr_review_done


## pr_fail

Fail the assembled-PR review with concrete issues. Transitions awaiting_pr_review -> needs_revision, routed back like a QA fail.

**Allowed roles:** pr_reviewer

**Composes:** pr_fail


## pr_pass

Pass the assembled-PR review. Transitions awaiting_pr_review -> awaiting_pm_review so the PM can merge.

**Allowed roles:** pr_reviewer

**Composes:** pr_pass


## reassign

Hand a claimed/in_progress task to another developer in your own cell. The branch is keyed to the task (not the agent), so it is preserved — the new developer continues the work-in-progress. No status change.

**Allowed roles:** cell_pm

**Composes:** (no atomic actions)


## resume

Resume a paused task you own. paused -> in_progress.

**Allowed roles:** cell_pm, developer, documenter, main_pm, qa

**Composes:** resume


## submit_root

Main PM opens the root→master PR and moves the root task to awaiting_pr_review for the main reviewer (the root analogue of the cell PM's submit_up). After pr_pass, call complete to escalate to the CEO. For branch-bearing roots (a Main-PM root-subtask assembles the cells' merged work); branchless coordination roots skip the gate and complete directly. The gate is branch-keyed, not task_type-keyed — a Main-PM root is planning-typed, never code.

**Allowed roles:** main_pm

**Composes:** submit_for_review

**Pre side effects:** create_root_pr

**Preconditions:** root_not_code


## submit_up

Cell PM opens the cell→root PR and moves the cell task into the PR-review gate (awaiting_pr_review). The cell reviewer reviews the assembled diff; after pr_pass the same Cell PM completes it.

**Allowed roles:** cell_pm

**Composes:** submit_for_review

**Pre side effects:** create_pr


## sync_branch

Rebase your task's branch onto its current base THROUGH the gate (raw git is denied). Use when your branch has fallen behind its base — e.g. a sibling task's PR merged into the parent branch while you worked. Fetches origin, rebases head onto base, and force-pushes (with-lease). No DB state change. On conflicts the rebase is aborted and the conflicted files are returned — resolve by hand, commit, then sync_branch again.

**Allowed roles:** developer

**Composes:** (no atomic actions)

**Preconditions:** owns_task, sync_branch_state


## triage

List actionable tasks in your scope.

**Allowed roles:** auditor, cell_pm, head_marketing, main_pm, product_owner

**Composes:** (no atomic actions)


## triage_all

List actionable tasks across all teams (Main PM only).

**Allowed roles:** main_pm

**Composes:** (no atomic actions)


## unblock

PM unblocks a blocked task; restores pre-block state.

**Allowed roles:** cell_pm, main_pm

**Composes:** unblock


## unclaim

Voluntarily release a claim back to pending. The work-in-progress branch is preserved.

**Allowed roles:** cell_pm, developer, documenter, main_pm, qa

**Composes:** (no atomic actions)

