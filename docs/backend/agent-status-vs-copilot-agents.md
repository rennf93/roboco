# Dispatcher and agent status exposure vs GitHub Copilot agents

GitHub's Copilot agents page pitches "a mission control center for everything your agents touch": one view to assign, steer and track agents. This audit checks what RoboCo exposes about the dispatcher and agent status, and whether one call or view answers "who is doing what, is the dispatcher healthy, what is it costing".

## What exists today

| Surface | Route | Source | Gives |
|---|---|---|---|
| Orchestrator runtime status | `GET /api/orchestrator/status`, `/agents/{id}`, `/waiting` | `Orchestrator.get_status_summary` (in-process `_instances`) | Live container state per agent, current task id, error count, started_at, waiting-for. No spend. |
| DB agent status | `GET /api/dashboard/agents/status` | `DashboardService.get_all_agent_status` | Persisted status (active/idle/offline) and `current_task_id` per agent, counts by status. No spend, no slug. |
| Spend per agent | `GET /api/usage/by-agent`, `/summary`, `/sessions`, `/projection` | `UsageService` | Tokens and cost per agent slug for 24h/7d/30d. No status or task. |
| Rate-limit parking | `GET /api/system/rate-limits` | `RateLimitStateTracker` | Providers currently parked and affected agents. |
| Spawn churn / respawn breaker | `GET /api/usage/spawn-waste` | `UsageService.get_spawn_waste` | Respawn-tracker strikes for wedged agent/task pairs. |
| Assign / steer | `POST /api/orchestrator/agents/{id}/spawn`, `/stop`, `/resolve-wait`, `/mark-waiting` | orchestrator routes | Manual spawn (task-aware prompt), stop, resolve a wait. |
| Company snapshot | `GET /api/cockpit/summary` | `CockpitService` | CEO-level business signals, not per-agent. |

## Comparison with the GitHub pitch

| GitHub pitch | RoboCo today | Gap |
|---|---|---|
| One view of all agents | Two disjoint endpoints (runtime vs DB) | Not unified; runtime vs DB can disagree |
| Each agent's current task | Both status endpoints carry it | Closed |
| Per-agent spend next to status | Separate `/usage/by-agent` | **Closed by this task**: `GET /api/dashboard/agents/overview` joins status, current task, tokens and cost per agent, with total cost and rate-limited providers |
| Budget alerts | Task and project budgets (see the budget audit doc) | Covered elsewhere |
| Assign and steer | Manual spawn/stop from the panel | Present; per-agent steering is spawn/stop only, no mid-run instruction channel except A2A DM from the CEO |
| Third-party agents (Claude, Codex, custom) | Pluggable providers (Claude, Grok, Gemini, Codex, Kimi...) | Present, no per-provider spend column in the overview |
| Dispatcher state (paused, breaker open, queue) | Not exposed | Open, see follow-ups |

## Gap fixed

`GET /api/dashboard/agents/overview?period=24h|7d|30d&team=` returns `{period, total, by_status, total_cost_usd, rate_limited_providers, agents: [{slug, name, role, team, status, current_task_id, total_tokens, cost_usd}]}`. Logic lives in `roboco/services/agent_overview.py` (`AgentOverviewService`), the route in `roboco/api/routes/dashboard.py` is a thin delegate, and it reuses `UsageService.get_by_agent` so spend math is not duplicated. Spend counts closed spawn sessions only, matching `/usage/by-agent`. Tests: `tests/unit/services/test_agent_overview.py`.

## Follow-ups (not done here)

1. **Dispatcher state endpoint (backend).** The dispatcher loop's queue depth, pending-claim holds, and the respawn breaker's open pairs live in orchestrator memory (`_pm_respawn_tracker`, `_instances`); only breaker strikes are visible via `spawn-waste`. There is no pause control or "dispatcher paused" flag to expose. A faithful endpoint needs the orchestrator to publish a snapshot (Redis or DB row) because the API process cannot read orchestrator memory. Larger than a thin route, so deferred.
2. **Runtime and DB status reconciliation (backend).** Merge `/orchestrator/status` container state into the overview. Needs the same orchestrator-to-API snapshot as item 1.
3. **Live spend for open sessions (backend).** The overview excludes in-flight sessions; task budgets already price open sessions live (`TaskService.task_spend_usd`). Reuse that to show running cost.
4. **Panel work (frontend follow-up).** The panel reads `agents/status`, `orchestrator`, `usage` and `rate-limits` through `panel/src/lib/api/{dashboard,orchestrator,usage,rate-limits}.ts` separately. A single mission-control page consuming the new overview endpoint is a frontend task; no panel code was changed here.

## Differentiators to keep in the positioning

RoboCo's value is not the single view but what sits behind it: roles, QA review, the in-path PR gate, the findings ledger and CEO approval before master.
