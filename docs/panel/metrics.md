# Metrics

The Metrics page (`/metrics`) is where you watch the company's throughput and its spend. Two tabs: **Performance** and **Token Usage** (the active tab is in the URL as `?tab=`).

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

## Next

→ [Cost & usage](../operations/cost-and-usage.md) for the pricing model and budget cap · [Health & metrics](../operations/health-and-metrics.md) for operational monitoring · [Command Center](./command-center.md) for the at-a-glance view.
