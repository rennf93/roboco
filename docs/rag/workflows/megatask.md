# MegaTask (Sequenced Batch) Reference

A **MegaTask** is several tasks the CEO described in one intake chat, shipped as one collision-sequenced batch — often across projects that do not share a codebase.

## Structure

- **Umbrella task** — groups the batch. It carries a `batch_id` and has no `parent_task_id`. It is **branchless**: no project, no branch, no PR of its own. It is the single board-review / CEO-approve / Main-PM-coordinate unit.
- **Root-subtasks** — one per piece of work. Each has the umbrella as its `parent_task_id`, shares the `batch_id`, and is a real coordination root with its **own project, branch, and PR**. They are sequenced into waves by cross-task dependencies.

Hierarchy: Umbrella (Main PM) → Root-subtasks (Main PM) → Cell tasks (cell PMs) → Dev subtasks. One extra Main-PM layer above the normal model.

## Rules an agent must know

- The umbrella does **no git**. It is exempt from the branch gate (it reaches `in_progress` with no branch) and you must **not** call `submit_root` on it — it assembles no PR. Each root-subtask opens and is reviewed on its own PR.
- The umbrella **completes** only when every root-subtask is terminal; then it escalates to the CEO (PR requirement waived).
- The root-subtasks are sequenced: a wave's tasks dispatch only once the previous wave's tasks reach a terminal state (ordinary dependency-gating). You do not reorder them — the analyzer set the order at create time.
- On the Board route the root-subtasks are held in `backlog` until the CEO approves the umbrella, then released to `pending`. On the Approve & Start route they start immediately. On Board-route activation a `code`-typed root-subtask is **retyped to `planning`** — a Main PM never owns a `code` task (the `main_pm + code` combo is the 2026-06-27 meltdown trigger).

## For the Main PM

You coordinate the umbrella exactly like a product coordination root: plan, delegate each root-subtask to its cell, and complete the umbrella once all root-subtasks finish. You do not branch or PR the umbrella itself. Because you may hold many roots in parallel, the single-task claim guards do not apply to you — only a genuine sequence dependency holds a task back.
