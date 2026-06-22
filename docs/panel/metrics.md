# Metrics

The Metrics page (`/metrics`) is where you watch the company's throughput and its spend. Three tabs: **Performance**, **Token Usage**, and **Delivery** (the active tab is in the URL as `?tab=`).

## Performance

The Performance tab is a snapshot of velocity and pipeline health, computed from the live task list and the orchestrator's agent status.

- **Velocity** — completed today, completed this rolling 7 days, total completed all-time, and a completion rate across all tasks.
- **Task Status** — counts of tasks that are pending, in progress, blocked, awaiting QA, and completed.
- **Agent Status** — how many agents are running, idle, waiting (need input), or in error.
- **Team Health** — one card per cell with a health score and its active / blocked / done breakdown. The score is a simple read on blockers: more blocked tasks pulls a cell's health down. A healthy cell sits near 100%; blockers visibly degrade it, so a cell sliding toward red is the signal to look at its [blockers](./command-center.md).

## Token Usage

The Token Usage tab is the cost dashboard, scoped to the last 24 hours unless a panel says otherwise.

- **Summary cards** — tokens in, tokens out, total tokens, total cost over 24h, the trend versus the prior period, and dollars saved by prompt caching.
- **Time series + model donut** — usage over time, and the split across models.
- **Per-agent and per-team bars** — who and which cell is spending.
- **Monthly projection** — projected monthly cost from a rolling-average daily run rate.
- **Cache efficiency** — cache hit rate and the cost it saved.
- **Sessions table** — the recent agent spawn sessions behind the numbers.

!!! tip "These panels update live"
    The token panels subscribe to a live usage stream over `/ws/system` and update in place as agents spend, falling back to periodic HTTP polling when the socket is down. You don't need to refresh to watch cost accrue.

### Where the dollar figures come from

Cost is derived from per-session token counts using provider-aware pricing — and local / Ollama usage is intentionally priced at **$0**, so a self-hosted or Ollama-routed workforce shows tokens but no dollars. The full cost model, the budget cap, and where each number originates are documented in [Cost & usage](../operations/cost-and-usage.md); this page only shows the numbers.

!!! note "Spend against budget lives on the scorecard"
    Metrics shows raw usage and projection. Your **monthly budget cap** and whether you're over it appear on the Company Scorecard in [Business](./business.md), not here.

## Delivery

The Delivery tab is the flow dashboard — not *what* the company shipped or what it cost, but *how the work moved*. Every panel is reconstructed from the task lifecycle history RoboCo already records (each status transition is logged), so it needs no extra bookkeeping. Cycle-time, bottlenecks, and rework look back 30 days; the scorecards look back 7.

- **Cycle Time by Stage** — the average time a task sits in each lifecycle stage (claimed, in progress, awaiting QA, awaiting documentation, awaiting PR review, awaiting PM review, …). This is where you see *where the time actually goes* — a tall "awaiting QA" bar means work waits on review, not on coding.
- **Bottlenecks** — the same data ranked by total time absorbed, with the single **worst stage** called out and a live count of how many tasks are **parked** in each stage right now, plus the current active-blocker count. It answers "what is holding the company up today?"
- **Rework** — how often work bounces back to `needs_revision` (the headline rate = reworked ÷ completed), broken down by cell and by agent, plus the token cost of that rework. Crucially, a bounce is attributed to the **QA or PR-reviewer who sent it back**, not the developer who owns the task — so a high `QA fails` number against a reviewer is a signal about *that reviewer's* gate, and a high rate against a developer is a signal about *their* first-pass quality.
- **Cell scorecards** — one card per cell (Backend / Frontend / UX-UI) with its completed count, average cycle time, rework rate, and cost over the last 7 days — the quick read on which cell is moving cleanly.

!!! tip "Reading rework attribution"
    A bounce charges the reviewer who rejected it via the `task.qa_fail` / `task.pr_fail` events, while the *rate* (`reworked / completed`) is computed against the task's owner. So one agent can show a low rate (good first-pass work) while another shows many `QA fails` (an active, rejecting gate) — both are healthy. Watch for a developer with a high rate **and** a reviewer with near-zero fails: that's a gate letting work through that later needs revision.

## Next

→ [Cost & usage](../operations/cost-and-usage.md) for the pricing model and budget cap · [Health & metrics](../operations/health-and-metrics.md) for operational monitoring · [Command Center](./command-center.md) for the at-a-glance view.
