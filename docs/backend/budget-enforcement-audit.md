# Budget enforcement and alerting audit (vs GitHub Copilot agents)

Context: the [Copilot agents page](https://github.com/features/copilot/agents) pitches a unified mission-control view, billing in Actions minutes plus AI Credits, and enterprise budget alerts. This audit covers how RoboCo enforces and alerts on task and project budgets. Everything below is gated by `ROBOCO_TASK_BUDGETS_ENABLED` (`roboco/config.py`, `task_budgets_enabled`); off means no cap is ever consulted.

## Where budgets live

| Budget | Field | Resolver / enforcer |
|---|---|---|
| Task cap | `tasks.budget_usd` | `effective_task_budget_usd` (`roboco/foundation/policy/agent_loop.py`); null means uncapped |
| Project monthly cap | `projects.monthly_budget_usd` | `project_budget_exceeded_guard` (`roboco/services/gateway/claim_guards.py`) |
| Per-session tool calls | `BudgetPolicy.tool_call_warn_at` / `_halt_at` | post-tool budget hook plus `AgentStopEngine` sweep |

## Enforcement: hard stop vs soft warn

- **Task `budget_usd`: hard stop.** `TaskBudgetEngine._task_budget_breach` (`roboco/runtime/engines/sweeps.py`) prices the active task's spend each budget sweep. On breach the task is BLOCKED (human resolver, budget marker) before the agent is gracefully stopped, then the CEO is notified (`notify_ceo_of_budget_breach`, APPROVAL/HIGH, ack required, Telegram actionable). `unblock` re-checks live spend and refuses while still over.
- **Project `monthly_budget_usd`: claim-time hard stop only.** Work-starting claims (`i_will_work_on`, `i_will_plan`) are refused once month spend reaches the cap; review/doc/gate claims are exempt so in-flight work can finish. It does not stop tasks already running.
- **Tool-call budget:** soft warn at 100 calls, halt at 300.

## Alerting

| Signal | Before this change | After |
|---|---|---|
| Task budget breached (100%) | CEO notification, task blocked | unchanged |
| Task budget nearing cap (80%) | **none** | one ALERT per task to the CEO (`notify_ceo_of_budget_warning`) |
| Project monthly cap reached | agent sees a claim refusal; CEO is not notified | unchanged (follow-up) |

## Spend visibility

- `GET /api/tasks/{id}` returns `spend_usd` (`TaskService.task_spend_usd`).
- `GET /api/projects/{id}` returns `monthly_spend_usd` (`TaskService.project_month_spend_usd`).
- Cockpit (`roboco/services/cockpit.py`) shows 30-day spend against the charter's `monthly_budget_cap` with an `over_budget` flag.
- Usage dashboard and rollups: `agent_spawn_sessions` to `daily_usage_rollups`, priced by `roboco/billing/pricing.py`, live via `USAGE_SNAPSHOT` on `/ws/system`.

## Comparison with the GitHub pitch

| Capability | GitHub Copilot agents | RoboCo | Gap |
|---|---|---|---|
| Budget alerts | Enterprise budget alerts | CEO ALERT at 80% and APPROVAL at 100% per task (new); none for project cap | Project-cap alert |
| Spend visibility | Billing in Actions minutes plus AI Credits | Per-task, per-project-month, per-agent, 30-day org, live dashboard, USD | No per-team/per-project budget dashboard view |
| Enforcement | Budgets/alerts | Task hard stop with block and human unblock; project claim gate | Project cap does not stop running tasks |
| Approval of overspend | Not part of the pitch | CEO decides: raise cap plus `unblock`, or cancel | RoboCo differentiator |

## Gap fixed in this change

No threshold alert existed: the CEO first heard about a task budget at the moment the task was stopped. Now `budget_warning_due` (`roboco/foundation/policy/agent_loop.py`, `BUDGET_WARN_FRACTION = 0.8`) fires inside the existing sweep; `NotificationDeliveryService.notify_ceo_of_budget_warning` sends one non-ack ALERT, deduplicated per task by subject and `related_task_id` (no migration), wrapped in `begin_nested()` so a failure never disturbs the sweep. No new route was needed because spend is already exposed. Tests: `tests/unit/runtime/test_task_budget_warning.py`.

## Follow-ups (not built)

1. **Project monthly-cap alert (80%/100%).** Needs a once-per-month dedup key (month plus project) and a sweep over projects rather than active tasks; larger than a single-task hook.
2. **Stop or drain running tasks when the project cap is hit.** Changes lifecycle semantics (which tasks to block, resume policy); needs a CEO decision.
3. **Configurable warn threshold.** 80% is a constant; per-project settings would need a migration and panel UI.
4. **Panel surface for warnings and a spend overview.** Frontend task; the warning currently reaches the CEO via the notification inbox and Telegram.
