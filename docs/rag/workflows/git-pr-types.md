# Git PR Types

There is no `is_root_pr` field or shortcut — every assembled PR now passes through the in-path **PR-review gate** (`awaiting_pr_review`) before a PM merges it. This page is the quick-reference; `docs/rag/lifecycle/status-transitions.md` and `docs/rag/architecture/review-findings.md` cover the mechanics in depth.

## The three PR kinds

| Kind | Opened by | Target | Gate reviewer | Merged by |
|------|-----------|--------|----------------|-----------|
| Leaf PR | Developer's `open_pr(task_id)` | The parent (cell) task's branch | none — QA reviews the diff directly, no PR-gate | Cell PM's `complete(task_id, notes)` |
| Cell→root PR | Cell PM's `submit_up(task_id, notes)` | The root task's branch | The cell's PR reviewer (be/fe/ux-pr-reviewer) via `pr_pass`/`pr_fail` | Cell PM's `complete(task_id, notes)`, after `pr_pass` |
| Root→master PR | Main PM's `submit_root(task_id, notes)` | The project's env-ladder **head rung** (`roboco.models.env_branches.head_branch`, typically `master` — never a literal string, always read through the shim) | The main PR reviewer (pr-reviewer-1) via `pr_pass`/`pr_fail` | The CEO, from the panel, after Main PM's `complete` escalates to `awaiting_ceo_approval` |

A leaf dev task and a branchless coordination root (product fan-out, MegaTask umbrella) skip the PR-review gate entirely — there's no assembled PR for a reviewer to gate.

## How PRs are created

There is **no** `roboco_git_create_pr` MCP tool. PRs are side-effects of lifecycle transitions, driven by the choreographer:

- **Leaf PR**: opened automatically when the assigned developer calls `open_pr(task_id)` after their `commit(...)` calls (`verifying -> awaiting_qa`).
- **Cell→root PR**: opened by `submit_up(task_id, notes)` — enters `awaiting_pr_review`.
- **Root→master PR**: opened by `submit_root(task_id, notes)` — enters `awaiting_pr_review`. Targets the project's **head rung**, not literal `master` — a project with no declared environment ladder resolves this from `projects.default_branch` via the read-time shim, so nothing changes for a project that hasn't opted into a multi-rung ladder. See `CLAUDE.md` "Env-branches ladder".

Title and body are generated from the task templates in `roboco/templates/git/pr_*.py`. Don't hand-write PR descriptions in the agent prompts — they'll be overridden.

## The PR-review gate (assembled PRs only)

`submit_up` and `submit_root` land the task on `awaiting_pr_review`, not directly on `awaiting_pm_review`. A reviewer must `claim_gate_review(task_id)` then verdict:

- `pr_pass(task_id, notes)` -> `awaiting_pm_review`, the PM merges via `complete`.
- `pr_fail(task_id, findings=[...])` -> `needs_revision`, routed back to the PM that submitted it, same as a QA fail.

`pr_pass` additionally refuses while the PR's own CI is red or unresolvable; a repo with no CI configured passes through cleanly. On non-GitHub forges the verdict is posted differently: GitHub and Gitea both support a real "request changes" review, but GitLab has no such primitive, so a `pr_fail` verdict on a GitLab-backed project posts as a plain MR note rather than a blocking review — the task still transitions to `needs_revision` normally regardless of forge. See `docs/rag/roles/pr-reviewer.md`.

## PR labels

Every fleet-opened PR is best-effort labeled with the org-structure vocabulary (`derive_pr_labels`, `roboco/foundation/policy/pr_labels.py`): `to master` (today, only the root→master PR) vs `to slave`, `root` for an assembled root PR, `MegaTask` for a batch-carrying task, and a layer label (`main-pm` / `cell/{team}` / `subtask/{team}`) — so a human triaging the PR queue on the forge sees which tree and org layer a PR belongs to at a glance.

## Auto-Checkout

Branches and checkout are handled automatically:

- `i_will_work_on(task_id)` (devs) creates the task's branch and checks it out in the agent's workspace.
- `i_will_plan(task_id, plan)` (PMs) does the same for parent tasks.
- Workspace dirty? The verb returns an error envelope; clean up first with `commit(...)` or escalate via `i_am_blocked(task_id, reason)`.

## Forge-agnostic

None of the above changes shape by forge — GitHub, Gitea, and GitLab (`projects.git_provider`) all route through the same `submit_up`/`submit_root`/`pr_pass`/`pr_fail` verbs and the same task states. Don't assume a PR lives at a `github.com` URL; `pr_url` always carries the real forge URL for whichever provider the project is registered against.
