# Response to GitHub's Copilot agents signal

Source: https://github.com/features/copilot/agents

GitHub's Copilot agents page pitches one unified view to assign, steer and track Copilot and third-party agents ("a mission control center for everything your agents touch"). Agent tasks are billed in Actions minutes plus AI Credits, and enterprise budget alerts are included. That is the same surface as RoboCo's dispatcher, panel and task/project budgets, so we audited both and recorded what changed. This note summarises the two audits: [budget-enforcement-audit.md](budget-enforcement-audit.md) and [agent-status-vs-copilot-agents.md](agent-status-vs-copilot-agents.md).

## Response decision

Close the small concrete gaps the audits found, document the larger ones as follow-ups, and do not compete on "many agents". RoboCo's position is organisation structure, stated below.

## What changed and why

### Budget alerts (budget audit)

Before this work the CEO first heard about a task budget at the moment the task was stopped: a breach blocked the task and notified the CEO, but nothing warned beforehand. GitHub pitches budget alerts, so this was a real gap.

- Added a threshold alert: `budget_warning_due` (`roboco/foundation/policy/agent_loop.py`, `BUDGET_WARN_FRACTION = 0.8`) fires inside the existing budget sweep.
- `NotificationDeliveryService.notify_ceo_of_budget_warning` sends one non-ack ALERT to the CEO per task, deduplicated by subject and `related_task_id`, so no migration was needed. It runs inside `begin_nested()` so a failure never disturbs the sweep.
- Tests: `tests/unit/runtime/test_task_budget_warning.py`.
- Unchanged: the 100% breach path (task BLOCKED, then an APPROVAL/HIGH CEO notification, and `unblock` refuses while spend is still over the cap). All of it stays gated by `ROBOCO_TASK_BUDGETS_ENABLED`.

### Unified agent status and spend view (dispatcher/status audit)

Status and spend were on separate endpoints: runtime state under `/api/orchestrator/*`, persisted status under `/api/dashboard/agents/status`, and spend under `/api/usage/by-agent`. There was no single call answering who is doing what and what it costs.

- Added `GET /api/dashboard/agents/overview?period=24h|7d|30d&team=`. It returns per-agent status, current task, tokens and cost, plus status counts, total cost and rate-limited providers.
- Logic lives in `roboco/services/agent_overview.py` (`AgentOverviewService`); the route in `roboco/api/routes/dashboard.py` is a thin delegate, and it reuses `UsageService.get_by_agent` so spend math is not duplicated. Spend counts closed spawn sessions only, matching `/usage/by-agent`.
- Tests: `tests/unit/services/test_agent_overview.py`.

## What was already there

The audits found existing coverage for most of the pitch, so no code was added for it:

- Spend visibility: per task (`spend_usd`), per project month (`monthly_spend_usd`), per agent, a 30-day org figure on the cockpit with an `over_budget` flag, and a live usage dashboard in USD.
- Enforcement: a task hard stop with human unblock, and a project monthly cap enforced at claim time.
- Third-party agents: pluggable providers (Claude, Grok, Gemini, Codex, Kimi and others).
- Assign and steer: manual spawn, stop and resolve-wait from the panel, plus A2A DM from the CEO. Per-agent steering is spawn/stop only; there is no mid-run instruction channel besides that DM.

## Remaining gaps (follow-ups, none built)

| # | Follow-up | Why it was not done here |
|---|---|---|
| 1 | Project monthly-cap alert at 80% and 100%. Today the CEO is not notified when a project cap is reached; the agent only sees a claim refusal. | Needs a once-per-month dedup key (month plus project) and a sweep over projects instead of active tasks; larger than a single-task hook. |
| 2 | Stop or drain running tasks when the project cap is hit. The cap only blocks new work-starting claims. | Changes lifecycle semantics (which tasks to block, resume policy); needs a CEO decision. |
| 3 | Configurable warn threshold. 80% is a constant. | Per-project settings need a migration and panel UI. |
| 4 | Dispatcher state endpoint (queue depth, pending-claim holds, open breaker pairs). Only breaker strikes are visible via `spawn-waste`; there is no pause flag. | The state lives in orchestrator memory the API process cannot read, so the orchestrator must publish a snapshot (Redis or DB row). Larger than a thin route. |
| 5 | Reconcile runtime and DB agent status in the overview. The two sources can disagree. | Needs the same orchestrator-to-API snapshot as item 4. |
| 6 | Live spend for open sessions in the overview, which currently counts closed sessions only. | Reuse `TaskService.task_spend_usd`, which already prices open sessions; deferred as a separate change. |
| 7 | Per-provider spend column in the overview, and a per-team/per-project budget view. | Not covered by the current overview shape or dashboards; scope deferred. |
| 8 | **Frontend follow-up:** a single mission-control panel page consuming `/api/dashboard/agents/overview`, and a panel surface for budget warnings and a spend overview. | The panel reads `agents/status`, `orchestrator`, `usage` and `rate-limits` separately (`panel/src/lib/api/{dashboard,orchestrator,usage,rate-limits}.ts`), and budget warnings currently reach the CEO only through the notification inbox and Telegram. No panel code was changed; this is a frontend task. |

## Positioning

RoboCo competes on organisation structure, not agent count. A unified dashboard and budget alerts are table stakes that GitHub sells and we have now closed the small gaps against; what sits behind the view is the differentiator.

- **Role separation.** Developers implement, QA reviews, documenters write docs, and PMs coordinate. No role does another's job, and the gateway rejects verbs outside a role's remit.
- **QA gate.** Leaf work goes through QA review against its acceptance criteria before it moves on. A bounce produces structured findings that must each be resolved before resubmission.
- **PR review gate.** Assembled cell-to-root and root-to-master PRs pass an in-path PR reviewer before the PM merges.
- **CEO approval.** Every merge to master requires CEO approval; the CEO also decides overspend (raise the cap and unblock, or cancel).

The budget audit makes the last point concrete: even overspend is a CEO decision rather than an automatic continuation, which the Copilot agents pitch does not mention.
