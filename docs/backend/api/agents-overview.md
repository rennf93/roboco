# `GET /api/dashboard/agents/overview`

One call that answers "who is doing what, and what is it costing": every agent's status, current task and spend, plus the providers currently rate-limited. Background and the comparison with GitHub's Copilot agents pitch live in `../agent-status-vs-copilot-agents.md`.

## Request

| Param | Type | Default | Notes |
|---|---|---|---|
| `period` | query, `24h` \| `7d` \| `30d` | `24h` | Spend window. Anything else returns 422 (regex-validated). |
| `team` | query, `Team` enum | none | Restrict to one team's agents. |

The route sits on the dashboard router, so it carries the same router-level panel-token gate as its siblings; there is no per-route role check.

## Response

```json
{
  "period": "24h",
  "total": 2,
  "by_status": {"active": 1, "idle": 1, "offline": 0},
  "total_cost_usd": 1.5,
  "rate_limited_providers": ["anthropic"],
  "agents": [
    {
      "slug": "be-dev-2",
      "name": "BE-Dev-2",
      "role": "developer",
      "team": "backend",
      "status": "active",
      "current_task_id": "<uuid or null>",
      "total_tokens": 10,
      "cost_usd": 1.5
    }
  ]
}
```

- `by_status` has one key per `AgentStatus` value, zero-filled.
- `status` is the persisted DB status (null falls back to `idle`), not live container state.
- Agents with no spend in the window report `total_tokens: 0`, `cost_usd: 0.0`.
- `total_cost_usd` is the sum of the listed agents, rounded to 6 decimals, so a `team` filter scopes it too.

## Implementation

- Logic: `AgentOverviewService.overview` in `roboco/services/agent_overview.py` (`get_agent_overview_service(db)` factory).
- Route: `get_agent_overview` in `roboco/api/routes/dashboard.py`, a thin delegate.
- Spend is reused from `UsageService.get_by_agent`, so it matches `/api/usage/by-agent`: closed spawn sessions only, sessions still running are not counted.
- Rate-limited providers come from `RateLimitStateTracker.list_rate_limited_providers`.
- Tests: `tests/unit/services/test_agent_overview.py`.

## Not included

Dispatcher state (queue depth, pending-claim holds, open respawn-breaker pairs), live container state, and live spend for open sessions are not in this response. Each is a tracked follow-up in the findings doc. No panel page consumes the endpoint yet.
