# Cost & usage

RoboCo measures what your workforce spends in tokens and dollars, and shows it to you live in the panel. This page explains where those numbers come from, how to read the dashboard, how each provider is priced, and what cost controls exist (and where they don't).

## How spend is measured

Spend is captured **per agent session** — one container, doing one stretch of work. Each spawned agent runs a small in-container SDK server that watches the agent's own transcript and exposes its running token counts (input, output, cache-read, cache-write). The orchestrator runs a background sweep every **~60 seconds**: for every active agent it pulls the live counts, writes a usage snapshot, and updates the open session row so the database reflects progress mid-run. When the container stops, the session is **finalized** — final token counts are resolved, priced through the built-in cost table, and written to the closed session row along with the exit reason.

A separate daily rollup keeps a rolling per-day, per-agent, per-team, per-model tally that feeds the "today" figures.

!!! note "Why mid-run numbers move, and why a crash can read $0"
    Because cost is computed per session and firms up only when the session ends, the figures for a still-running agent update roughly every minute and aren't final. A session that crashes or is abandoned before its transcript is read can finalize at a low or zero value. The dashboard is an accurate ledger of *closed* sessions plus a live estimate for the ones still open — not a real-time invoice.

Grok agents have no live SDK hook; the orchestrator reads each Grok container's `usage.json` instead, and uses the same file to enforce the per-agent Grok cost cap (below).

## The Token Usage & Cost dashboard

Two panel surfaces show spend:

- The **command center** carries a **Token Usage & Cost** card that streams live while agents work. It listens on the `/ws/system` WebSocket and shows a **Live** badge when connected; if the socket drops it falls back to HTTP polling and shows **Polling** (or **Connecting**). See [the command center](../panel/command-center.md).
- The **Metrics** page has a dedicated **Token Usage** tab with the full breakdown. See [Metrics](../panel/metrics.md).

A **24h / 7d / 30d** selector drives every panel. What you get:

| Panel | What it shows |
|-------|----------------|
| Summary | Input, output, and total tokens for the period, plus total cost in USD |
| Trend | Percent change vs the immediately prior window of equal length |
| Time series | Hourly points for 24h; daily points for 7d/30d |
| Per-model | Donut of cost share by model |
| Per-agent / per-team | Bar charts of spend, each with its share of the total |
| Monthly projection | Forecast spend (see below) |
| Cache efficiency | Cache hit-rate and the dollars prompt-caching is saving |
| Recent sessions | Raw recent spawn-session rows (default 50) |

!!! info "Totals count all four token classes"
    The total-token and cost figures sum **input + output + cache-read + cache-write**. Prompt-caching reads are cheap but not free, so they show up in both the totals and the cache-savings panel.

!!! warning "Projection and cache savings are estimates"
    The monthly projection is a naive extrapolation: the average daily cost over the last 7 days × 30. It's a planning forecast, not a bill, and it overstates if you spun up the fleet for a one-off burst. The cache-savings figure uses a single aggregate baseline rate to estimate what the cached tokens *would* have cost at the full input price — treat it as indicative, not exact.

## How each provider is priced

Pricing is provider-aware, from a built-in USD-per-million-token table (`roboco/billing/pricing.py`). Model names are matched on substring, longest fragment wins.

| Provider / model | Input | Output | Priced? |
|------------------|-------|--------|---------|
| Claude Opus 4 | $5.00 | $25.00 | yes (cache read $0.50, write $6.25) |
| Claude Sonnet (4 / 3.7 / 3.5) | $3.00 | $15.00 | yes (cache read $0.30) |
| Claude Haiku 4 / 3.5 | $1.00 | $5.00 | yes |
| xAI `grok-build` | $1.00 | $2.00 | yes (cache read $0.20) |
| Local Ollama (`ollama/…` or bare tag) | — | — | **$0 by design** |
| Ollama Cloud (`:cloud` tag) | — | — | **$0 by design** |

!!! note "$0 for local and Ollama Cloud is not a bug"
    Self-hosted Ollama runs on hardware you own and Ollama Cloud is billed by flat subscription, so neither carries a per-token cost. RoboCo intentionally prices them at $0 — it is not undercounting, and you won't see a warning.

!!! warning "A brand-new Claude model can silently undercount"
    If a `claude`-named model isn't in the pricing table, RoboCo logs a warning and returns $0 for it rather than crashing. That **is** real spend going uncounted. If you point an agent at a Claude model newer than this build and see $0 cost on a busy fleet, the table needs the new rate added — check the orchestrator logs for the pricing warning.

## Cost controls (and the asymmetry)

There is exactly one built-in dollar cap, and it only covers Grok:

| Setting | Default | What it does |
|---------|---------|--------------|
| `ROBOCO_GROK_MAX_COST_USD` | `0.0` | Per-agent Grok cost ceiling in USD, read from the container's `usage.json`. The orchestrator kills a Grok container once it crosses this, catching runaway-loop token burn. `0` disables it. **Grok agents only.** |

!!! danger "There is no built-in dollar cap for Claude agents"
    The Claude path is **observe-only** on cost. RoboCo will show you Claude spend live and historically, but it will not auto-kill a Claude agent for crossing a dollar threshold — there is no `ROBOCO_*_MAX_COST_USD` equivalent for it. Your protection against runaway Claude spend is the structural one: rate-limit and overload **park-and-probe** (a provider 429 or persistent overload queues the agent and probes for recovery instead of retrying in a hot loop — see [provider resilience](../models/resilience.md)), the per-task verb gateway, and watching the dashboard. Budget accordingly.

A related guard reaps abandoned interactive chats so they don't leak a container:

| Setting | Default | What it does |
|---------|---------|--------------|
| `ROBOCO_INTERACTIVE_IDLE_REAP_SECONDS` | `1800` | Reaps an idle live Intake/Secretary chat (by time since last turn) so it stops holding a container and burning tokens. `0` disables. |

## Quick checks from the shell

```bash
# Period summary (totals + cost + trend)
curl -s 'http://localhost:3000/api/usage/summary?period=7d'

# Monthly projection
curl -s http://localhost:3000/api/usage/projection

# Container-level resource use (not token cost)
docker stats
docker system df
```

## Next

- Read the live dashboard on [the command center](../panel/command-center.md) and the full breakdown on [Metrics](../panel/metrics.md).
- See where the live stream comes from in [WebSockets](../api/websockets.md) and the `/api/usage/*` routes in the [REST API](../api/rest-api.md).
- Understand the park-and-probe spend protection in [provider resilience](../models/resilience.md).
- For operational (non-cost) health, see [health & metrics](./health-and-metrics.md).
