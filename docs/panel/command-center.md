# Command Center

The Command Center at **`/overview`** is the panel's home page — the one screen you open to see whether the company is healthy and to act on the two things only you can decide. It pulls everything onto a single board: per-cell health, your decision queues, live metrics and cost, the auditor's open flags, current blockers, and a recent-activity feed. The notifications bell in the top header rides along on every page.

## Team Health

The top section shows a **health card per cell** — Backend, Frontend, UX/UI, and the management line — so you can read the state of the whole workforce at a glance before you scroll into anything detailed. It comes from the CEO-overview endpoint and refreshes with the rest of the board.

A **Quick Actions** bar sits directly under Team Health so the common jumps (author a task, open the kanban, and so on) are reachable without scrolling.

## The two CEO decision surfaces

The Command Center is where the company hands work *up to you*. Two panels exist specifically for that.

### CEO Approval Queue

Every task that a Main PM escalates lands in **`awaiting_ceo_approval`** and appears here. For each one you can:

- **Approve & merge** — the PR is merged and the task moves to `completed`.
- **Request changes** — the task drops to `needs_revision` and goes back to the cell.
- **Cancel** — the task is cancelled.

These are the same actions you'd take from the [Task Detail page](./tasks-and-kanban.md#ceo-god-mode), surfaced here so the day's approvals are in one list. See [the task lifecycle](../company/task-lifecycle.md) for how a task reaches this state.

### PR Review Queue

This panel lists **inbound external / fork pull requests** RoboCo discovered on your repositories. For each one you decide:

- **Supersede** — let an internal task take the change forward.
- **Dismiss** — close it out.

!!! note "Hidden when empty"
    The PR Review Queue only renders when there's something to decide. An empty board means no external PRs are waiting — not that the feature is off. (Inbound external/internal PR review is itself flag-gated; see [PR review](../optional/pr-review.md).)

## Metrics, alerts, and usage

A three-up row gives you the operational pulse:

- **Key Metrics** — the headline counts from the overview endpoint.
- **Auditor Alerts** — currently *unresolved* flags raised by the [Auditor](./auditor.md). This is a read-only nudge; the full feed lives on the Auditor page.
- **Usage overview** — a live token/cost summary. It updates off the `USAGE_SNAPSHOT` stream on `/ws/system` and falls back to polling when that socket is down. The full breakdown — 24h cost, monthly projection, cache savings — is on the [Metrics page](./metrics.md).

### Strategy Signals

Next to the Approval Queue sits a **Strategy Signals** panel. It surfaces charter/strategy-drift notices from the strategy engine, which is **off by default** and armed by `ROBOCO_STRATEGY_ENGINE_ENABLED` (toggled from [Settings → Feature Flags](./settings.md)). With the engine off, the panel simply has nothing to show. See [the strategy engine](../optional/strategy-engine.md).

## Blockers and activity

The bottom row pairs:

- **Active Blockers** — tasks currently sitting in `blocked`, so a stuck dependency doesn't go unnoticed.
- **Recent Activity** — a rolling feed of what the company did in the last 24 hours.

## Notifications bell

The bell in the top **Header** (present on every dashboard page, not just here) is your inbox for formal notifications — the acknowledgement-required signals PMs and the Board send. Clicking through takes you to the full [Notifications inbox](./communications-and-journals.md#notifications) at `/notifications`, where you can mark items read, acknowledge those that need it, and clear the unread count.

!!! tip "Backend down?"
    If the orchestrator isn't running, the Command Center (like every page) renders a clear offline state with a **Retry** — it detects a refused connection rather than spinning. Bring the stack up and retry; see [common issues](../troubleshooting/common-issues.md).

## Next

→ [Tasks & Kanban](./tasks-and-kanban.md) to act on individual work, or [Agents & work sessions](./agents-and-work-sessions.md) to watch the workforce run.
