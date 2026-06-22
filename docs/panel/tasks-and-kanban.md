# Tasks & Kanban

Tasks are the unit of work in RoboCo — nothing happens without one. The panel gives you three views of them: the **Tasks table** (`/tasks`) for searching and filtering everything, the **Task Detail** page (`/tasks/[id]`) for the full state of one task plus your god-mode overrides, and the **Dev Kanban** (`/kanban`) for a pipeline-style board of where work is in flight.

## The Tasks table (`/tasks`)

The master list of every task. It gives you full-text search and multi-select filters across **status, team, task type, project, and product**, with sortable, paginated, expandable rows. All of that filter/sort/page state lives in the URL query string, so a filtered view is shareable and survives the back button.

The **Create Task** dialog lets you author a task by hand — title, acceptance criteria, project/product, type. If you'd rather describe a rough idea and have an agent read your code and draft a properly-formed task with acceptance criteria, use the conversational [Task Assistant](../get-started/first-task.md) at `/prompter` instead.

## Task Detail (`/tasks/[id]`)

The heaviest operator surface in the panel. The header carries the task's metadata; the body is tabbed (notes, commits, acceptance criteria, and more).

### Clickable branch and PR

When a task has a branch or a pull request, the git badges are **live links** into your repository's web host — the branch jumps to `…/tree/<branch>` and the PR to `…/pull/<n>`, derived from the project's git URL. If the URL can't be parsed into a web link, it falls back to a plain label rather than a broken link, so you always see *something* useful.

### Per-role note sections

The **Notes** tab carries a separate, editable section for each role that touches a task, so the handoff trail is structured rather than a single free-text blob:

| Section | Written by |
|---------|-----------|
| **Developer Notes** | the dev (`dev_notes`) |
| **Documenter Notes** | the documenter (`doc_notes`) |
| **QA Notes** | QA (`qa_notes`) — with a pass/fail verdict pill from `qa_verified` |
| **PR Reviewer Notes** | the PR reviewer (`pr_reviewer_notes`) |
| **Auditor Notes** | the Auditor (`auditor_notes`) |

Empty sections still render so you can see what's expected — and add a note yourself if you need to.

### CEO god-mode { #ceo-god-mode }

The lifecycle is normally enforced role-by-role at the [gateway](../company/agent-gateway.md), but *you* are the CEO and can override any of it from this page. The action set covers the whole lifecycle: claim, start, pause, resume, block, unblock, verify, submit-qa, pass-qa / fail-qa, docs-complete, submit-pm-review, approve-and-merge, escalate-to-ceo, ceo-approve / ceo-reject, and cancel. You can also run git directly — **create a branch, create a PR, merge a PR** — and those operations are performed as agent id **`ceo`**.

This page also exposes **Approve & Start** for a board-reviewed pending task and **Re-draft with board feedback**, which routes back to the Task Assistant (`/prompter?redraft=`).

!!! warning "Every override forces an audit note"
    The state-changing actions — pass/fail QA, approvals, rejections, cancellation — open a dialog that requires you to type a note before they go through. That note is written permanently to the task's history. God-mode is powerful by design; it is never silent.

See [the task lifecycle](../company/task-lifecycle.md) for what each transition means and [the merge model](../company/merge-model.md) for how PRs flow up to master.

## Dev Kanban (`/kanban`)

A swim-lane board of the delivery pipeline, switched with the `?view=` query param into four boards:

| View | `?view=` | Shows |
|------|----------|-------|
| **Developer** | `dev` | tasks in the development states |
| **QA** | `qa` | tasks awaiting / in QA review |
| **PR Review** | `pr-review` | assembled PRs at the in-path review gate |
| **PM** | `pm` | tasks awaiting PM review and merge |

Each board is a read-at-a-glance view of where work sits in the [lifecycle](../company/task-lifecycle.md). Switching tabs updates the URL, so a specific board is shareable.

## Next

→ [Agents & work sessions](./agents-and-work-sessions.md) to see who's working these tasks, or [Git](./git.md) to inspect the branches and PRs they produce.
