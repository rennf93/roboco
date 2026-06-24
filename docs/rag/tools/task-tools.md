# Task Management Tools

There is **no** `roboco_task_*` tool surface. Tasks move through the lifecycle via **flow verbs** on the `roboco-flow` MCP server. Each verb is role-scoped — you only see the ones your role is allowed to call (the spawn manifest registers them per role). Every verb returns an **Envelope** whose `next` field tells you what to call next; trust it rather than guessing state.

The verbs below are grouped by who calls them.

## Developer flow

```python
give_me_work()                  # returns your most-actionable pending task
i_will_work_on(task_id, plan="...")
                                # claims + sets plan + starts; auto-creates and
                                # checks out feature/{team}/{task-hierarchy}
commit(message, files=None)     # content tool — repeat per change (auto-pushed)
open_pr(task_id)                # pushes branch + opens the PR
i_am_done(task_id, notes="")    # verifying -> awaiting_qa (PR must already be open)
i_am_blocked(task_id, reason)   # external dependency; cell PM unblocks
unclaim(task_id)                # release a claimed task back to the queue
resume(task_id)                 # recover a paused task after compact/restart
i_am_idle()                     # no work in your queue right now
```

There is no separate claim / start / pause verb — `i_will_work_on` composes claim + set-plan + start atomically, and `i_am_done` composes verify + submit-qa. Branches are auto-created on `i_will_work_on`; do not checkout by hand.

## QA flow

```python
give_me_work()                  # returns an awaiting_qa task
claim_review(task_id)           # claim for review (auto-checks-out dev branch)
pass_review(task_id, notes)     # awaiting_qa -> awaiting_documentation
fail_review(task_id, issues=[...])
                                # awaiting_qa -> needs_revision (dev gets it back)
unclaim(task_id) / resume(task_id) / i_am_idle()
```

`notes` (on pass_review) and `issues` (on fail_review) must be substantive — the enforcement layer rejects empty or near-empty content. QA cannot review its own dev work (self-review guard rejects on `claim_review`).

## Documenter flow

```python
give_me_work()                  # returns an awaiting_documentation task
claim_doc_task(task_id)         # claim the doc phase
commit(message, files)          # commit the doc files you write
i_documented(task_id, notes, files)
                                # awaiting_documentation -> awaiting_pm_review
```

Documentation tasks are **not** delegated — the lifecycle auto-creates the doc phase after a code task passes QA.

## Cell PM flow

```python
triage()                        # list actionable tasks in your cell
i_will_plan(task_id, plan, approach)
                                # claim + plan + start a parent task
delegate(parent_task_id, title, description, assigned_to, team, task_type,
         nature, estimated_complexity, acceptance_criteria,
         covers_parent_criteria=[...])
                                # create a subtask; covers_parent_criteria maps
                                # it to the parent ACs it is responsible for
reassign(task_id, assigned_to)  # move a subtask to a different agent
unblock(task_id, reason)        # blocked -> in_progress (PM only); reason is
                                # recorded as your journal:decision (no separate
                                # note needed)
submit_up(task_id, notes)       # open cell->root PR; -> awaiting_pr_review
                                # (the cell PR reviewer gates it; after pr_pass
                                #  the same Cell PM completes + merges)
complete(task_id, notes)        # awaiting_pm_review -> completed (merges leaf PR)
escalate_up(task_id, reason)    # escalate to your escalation target
```

After `i_will_plan` and each `delegate`, the envelope includes a coverage view of the parent — `parent_ac_coverage` (per-criterion `id` / `text` / `claimed` / `verified`) and `unclaimed_parent_acs` (criteria no subtask covers yet). A parent cannot idle with unclaimed criteria, nor `complete` / `submit_up` / `escalate_to_ceo` until every criterion traces to a child that passed QA. These gates stay inert until you start declaring `covers_parent_criteria`. See `docs/rag/workflows/task-planning.md`.

**Delegation rules** (enforced): `main_pm -> cell_pm`; `cell_pm -> its team's devs`. Cell PMs receive planning-typed parent tasks; devs get code/research (UX devs also design). Always create subtasks via `delegate` with `parent_task_id` set — there is no standalone task-create verb for agents.

## Main PM flow

The Main PM shares most Cell PM verbs (`i_will_plan`, `delegate`, `complete`, `unblock`, `triage`, `escalate_up`), **adds** the verbs below, and — unlike a Cell PM — has **no** `submit_up` or `reassign`. Its bubble-up verb is `submit_root` (the root analogue of the Cell PM's `submit_up`):

```python
triage_all()                    # list actionable tasks across all teams
submit_root(task_id, notes)     # open root->master PR; -> awaiting_pr_review
                                # (the main PR reviewer gates it; after pr_pass,
                                #  complete escalates to the CEO)
escalate_to_ceo(task_id, reason)
                                # awaiting_pm_review -> awaiting_ceo_approval
give_me_work()                  # Main PM may also pull work directly
```

For a code root the Main PM **must** `submit_root` first — that opens the root→master PR and enters the in-path gate (`awaiting_pr_review`); only after the main reviewer `pr_pass`es it does `complete` escalate to the CEO. A branchless coordination root (product fan-out, no repo) skips the gate and is completed/escalated directly. The Main PM never merges to `master` — `complete` escalates and only the CEO merges the root→master PR.

## Board flow (Product Owner / Head of Marketing)

```python
triage()                        # list actionable tasks in scope
escalate_to_ceo(task_id, reason)
i_am_idle()
```

The Board **cannot** claim, create, complete, or cancel tasks. Strategic decisions are escalated to the CEO.

## Auditor flow

```python
triage()                        # read-only list of actionable tasks
i_am_idle()
```

The Auditor is a silent observer: read-only `triage`, no `say`/`dm`/ `notify`, no claim/complete/cancel.

## PR Reviewer flow

```python
give_me_work()                  # returns an inbound-PR review task
claim_pr_review(task_id)        # claim it (planless, branchless — read-only)
post_pr_review(task_id, ...)    # posts one change-request on the PR; task -> completed
i_am_idle()
```

The PR Reviewer reviews inbound external/fork (and, behind a flag, internal) PRs the org did not open. It is read-only: no `commit`/`open_pr`/`merge`, no `say`/`dm` — the change-request is posted server-side on the PR itself, and the CEO decides Supersede/Dismiss from the PR Review Queue.

The same role also runs the **in-path PR-review gate** on the org's own assembled delivery PRs — the merge-level review before the PM merges:

```python
claim_gate_review(task_id)      # claim an awaiting_pr_review task; returns the assembled diff
pr_pass(task_id, notes)         # assembled PR is correct -> awaiting_pm_review (the PM merges)
pr_fail(task_id, issues)        # send it back -> needs_revision, like a QA fail
```

Both verdicts are also posted on the assembled PR itself as a GitHub review (server-side, bot account) so the decision is visible on the PR the PM merges: `pr_pass` → APPROVE, `pr_fail` → REQUEST_CHANGES — except the root→master PR, which only ever gets a plain COMMENT (only the CEO acts on `master`).

A cell reviewer (be/fe/ux-pr-reviewer) reviews its cell's assembled cell→root PR; `pr-reviewer-1` reviews the root→master PR for the cross-cell integration seam, before the CEO sees it.

## Cancel

Cancelling a task (any non-terminal status -> `cancelled`) is restricted to **PM roles and the CEO**. There is no agent verb to cancel — it is a PM/CEO operation through the lifecycle.

## Progress

Record progress against your plan with the `progress` content tool (on `roboco-do`), not a task verb:

```python
progress(task_id, message="API skeleton landed", plan_step="2")
```

Your plan's steps are the progress checklist; the percentage is derived from completed steps — you do not set it.
