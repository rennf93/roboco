# CEO Portfolio Endpoint (`GET /api/dashboard/portfolio`)

> **PR:** #966 (task `1e0b4c7b`, the assembled portfolio branch) — **Status:** QA-passed
> **Leaf delivery:** sibling task `b3960175`, PR #965
> **Related:** `docs/map/api-routes-schemas.md` (route table), `docs/map/metrics-observability.md` (MetricsService slice)

## Overview

The CEO governs 5 products (roboco-api, cc-tg-hub-be, discord-vexa-bridge, guard-core-saas, vexa-ai), but every pre-existing dashboard endpoint was per-team or org-wide — there was no cross-project aggregation. `GET /api/dashboard/portfolio` fills that gap: one call returns a per-project metrics row for every governed project so the CEO can see the whole fleet's delivery, rework, open findings, and budget burn side by side.

The endpoint is **CEO-gated**: any authenticated non-CEO panel token receives `403`. It sits on the existing `/api/dashboard` router, which is router-level panel-gated like the rest of the dashboard surface; the CEO check routes through the shared `require_ceo_role` helper (roboco/api/deps.py), the single source of truth for route-level CEO gates.

## Endpoint

```
GET /api/dashboard/portfolio?days={1..90}
```

| Parameter | Default | Constraints | Meaning |
|---|---|---|---|
| `days` | 30 | `ge=1, le=90` | Trailing window for median lead time and rework rate (query validation returns `422` outside the range) |

Auth: panel token (router-wide `Depends(require_panel_token)` pattern) **plus** the shared `require_ceo_role` gate (`action="view the project portfolio"` → `403`, detail `"Only the CEO may view the project portfolio"`). No agent role may read it.

## Response

`list[PortfolioProjectMetrics]` (`roboco/api/schemas/dashboard.py:95`), sorted by `active_task_count` **descending** — the most active project first.

```json
[
  {
    "project_id": "3f9c1e2a-...",
    "project_slug": "roboco-api",
    "project_name": "Roboco API",
    "active_task_count": 12,
    "median_lead_time_hours": 6.4,
    "rework_rate": 0.25,
    "open_findings_count": 3,
    "monthly_budget_burn_usd": 41.25
  }
]
```

| Field | Type | Semantics |
|---|---|---|
| `project_id` / `project_slug` / `project_name` | UUID / str / str | Project identity from `ProjectTable`. |
| `active_task_count` | int | Non-terminal, non-backlog tasks (see "Active set" below). |
| `median_lead_time_hours` | float \| null | Median created→completed lead time over tasks completed **within the `days` window**; `null` when nothing completed in the window. |
| `rework_rate` | float | Fraction of those windowed completed tasks with `revision_count > 0`; `0.0` when there are no completions (never a division error). |
| `open_findings_count` | int | Open (`status='open'`) review findings on the project's tasks, all-time (not windowed). |
| `monthly_budget_burn_usd` | float | This **calendar month's** agent-spawn cost for the project's tasks (not a trailing 30d window). |

Every project appears exactly once — a project with no activity still shows, zeroed — because `DashboardService.get_portfolio` starts from the *project list* and merges metrics onto it, rather than deriving rows from task activity. The CEO sees the whole governed fleet, not only busy repos. Tasks with a null `project_id` (board/product tasks) never appear: the view is grouped *by* project.

## Aggregation internals

All arithmetic is **server-side**, in `MetricsService.get_portfolio_metrics` (`roboco/services/metrics.py:1463`); the route handler is a thin mapper. The method returns `{project_id: metrics-dict}` over four aggregation slices, and `DashboardService.get_portfolio` (`roboco/services/dashboard.py:284`) merges project identities, defaults any project absent from a slice to a zeroed row (`_default_portfolio_row`), and sorts.

Four queries total, all `GROUP BY tasks.project_id` (the existing `ix_tasks_project_status` index on `(project_id, status)` backs the active-count slice):

1. **Active tasks** — one grouped count of tasks whose status is not in `_PORTFOLIO_HELD_STATUSES` = `{BACKLOG, COMPLETED, CANCELLED}` (`metrics.py:113`). Backlog is parked, not active; cancelled never was.
2. **Median lead time + rework rate** — one pass over completed tasks in the `days` window, reading `completed_at`, `created_at`, and `revision_count` from the **same rows**, so both figures and the window always describe one population. Lead time is `(completed_at - created_at)` in hours, rounded to 2; the **median** is used deliberately because agent-run lead times are heavy-tailed and a mean misrepresents them. Rework rate rounds to 4 decimals. Rows are fetched into Python rather than SQL-aggregated because the median needs the per-row values anyway.
3. **Open findings** — SQL-grouped count from `TaskReviewFindingTable` joined through the task's project, filtered to `status = 'open'`. This deliberately does **not** reuse `ReviewFindingsRepository.list_open_findings`, which is a capped dashboard-queue fetch and would silently undercount above its row cap.
4. **Monthly budget burn** — `_portfolio_spend_by_project` (`metrics.py:145`): `AgentSpawnSessionTable` joined to `TaskTable` by string-cast task id, filtered to sessions started on/after the 1st of the current month (UTC), grouped by task project. Pricing mirrors `TaskService.project_month_spend_usd`: closed sessions use their stored `estimated_cost_usd`; sessions still open (no `ended_at`) are priced **live** from their token counts via `roboco.billing.pricing.calculate_cost`. The whole fleet is batched in **one query** instead of one query per project.

### Edge behaviors

| Situation | Result |
|---|---|
| Project with zero activity | Appears once, all metrics zeroed / `null` lead time. |
| Nothing completed in the window | `median_lead_time_hours = null`, `rework_rate = 0.0`. |
| Rounding | Lead time to 2 decimals, rework rate to 4, burn to 4. |
| Null-project tasks | Excluded from every slice (filtered server-side). |
| `days` window vs calendar month | Lead time / rework honor `days`; burn is always month-to-date regardless of `days`. |

## Testing

`tests/integration/test_dashboard_routes.py`, two tests against a real Postgres:

- `test_portfolio_aggregation` (line 881) — seeds two projects with disjoint task/findings/spend rows via `_seed_portfolio_projects` (line 744): Alpha with 2 active, 2 completed (1h + 4h leads, one reworked), 2 open + 1 addressed finding, $2.50 burn; Beta with 1 active + 1 cancelled, 1 open finding, $0. Asserts every field on both rows (including the cancelled-task exclusion and the `null` lead time), the descending `active_task_count` order, and the presence of all 8 response keys. The seeded spend session is clamped to the current calendar month so the test is month-boundary-safe.
- `test_portfolio_is_ceo_only` (line 928) — a `non_ceo_client` fixture overrides the agent context to `AgentRole.DEVELOPER`; asserts `403` with a detail mentioning "CEO".

## Placement (per `.roboco/conventions.yml`)

| Definition | Module | Note |
|---|---|---|
| `PortfolioProjectMetrics` (Pydantic response schema) | `roboco/api/schemas/dashboard.py:95` | Schemas forbidden in routes |
| `ProjectPortfolioMetricsData` (internal dataclass) | `roboco/models/dashboard.py:55` | Models forbidden in routes |
| `MetricsService.get_portfolio_metrics`, `_portfolio_spend_by_project`, `_PORTFOLIO_HELD_STATUSES` | `roboco/services/metrics.py:1463/145/113` | Business logic, never the route |
| `DashboardService.get_portfolio` | `roboco/services/dashboard.py:284` | Orchestration + identity merge + sort |
| `get_portfolio_projects` (thin handler + 403 guard) | `roboco/api/routes/dashboard.py:327` | Route delegates to services; contains no model/helper |

## Related

- `docs/map/metrics-observability.md` — the MetricsService slice this extends.
- `roboco/services/metrics.py:1463` `get_portfolio_metrics` — the aggregation; `:145` `_portfolio_spend_by_project` — the burn query; `:113` `_PORTFOLIO_HELD_STATUSES` — the active-set definition.
- `roboco/services/dashboard.py:284` `get_portfolio` — identity merge + sort.
- `roboco/api/routes/dashboard.py:327` `get_portfolio_projects` — the gated route.
- The pre-existing per-agent/per-team scorecard family (`GET /api/dashboard/metrics/…`) remains per-agent/per-team; this endpoint is the first grouped by `tasks.project_id`.