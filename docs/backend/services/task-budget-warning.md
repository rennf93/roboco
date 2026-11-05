# Task budget 80% CEO warning

Before this change the CEO first heard about a task cost budget when the task was already stopped and blocked at 100%. The budget sweep now sends a one-time heads-up when spend crosses 80% of the cap, so the budget can be raised before the interruption. The wider audit and the comparison with the GitHub Copilot agents pitch live in [budget-enforcement-audit.md](../budget-enforcement-audit.md).

Everything here is gated by `ROBOCO_TASK_BUDGETS_ENABLED`. Tasks with no `budget_usd` (null) are never warned about.

## How it works

| Piece | Location | Role |
|---|---|---|
| `BUDGET_WARN_FRACTION = 0.8`, `budget_warning_due(cap_usd, spend_usd)` | `roboco/foundation/policy/agent_loop.py` | Pure policy. True only while `0.8 * cap <= spend < cap` and `cap > 0`. |
| `TaskBudgetEngine._warn_task_budget_nearing_cap` | `roboco/runtime/engines/sweeps.py` | Called from the existing per-task budget check, right after spend is priced. |
| `NotificationCEONotices.notify_ceo_of_budget_warning` | `roboco/services/notification_delivery.py` | Builds and delivers the notification; returns `True` only if one was sent. |

## Behavior

- **Window.** The warning fires at or above 80% of the cap and below 100%. At or past the cap the existing breach path owns the alert: the task is blocked and the CEO gets the APPROVAL/HIGH notification that requires an ack.
- **Notification.** Type `ALERT`, priority `NORMAL`, no ack required, nothing is blocked. Subject is `Budget warning: <task title>`. The body states the percentage used, spend and cap in USD, and tells the CEO to raise the task's Budget (USD) to avoid the stop at 100%.
- **Once per task.** Deduplication looks for an earlier notification with the same `related_task_id` and the same subject. No migration or new column is involved. Consequence: renaming the task after a warning was sent changes the subject, so a later warning could be sent again for that task.
- **Best-effort.** The call is wrapped in `db.begin_nested()` and any exception is logged (`task budget warning failed`) and swallowed, so a notification failure never disturbs the budget sweep or the hard stop.
- **No CEO agent.** If no CEO agent exists, nothing is sent and the method returns `False`.

## Not covered

The project `monthly_budget_usd` cap has no threshold alert (it only refuses new work-starting claims). That, a configurable threshold, and a panel surface for warnings are listed as follow-ups in the audit doc.

## Tests

`tests/unit/runtime/test_task_budget_warning.py` covers the policy window boundaries (including a zero cap), the sweep hook firing only inside the window, failure swallowing, and the once-per-task dedup in the delivery service.
