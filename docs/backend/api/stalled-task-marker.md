# Durable Stalled-Task Marker + Read Endpoint

## Overview

When the dispatcher's respawn breaker (`_pm_respawn_should_gate` in `roboco/runtime/orchestrator.py`) gives up on a task, the per-agent strike count exceeds `_PM_RESPAWN_MAX_UNPRODUCTIVE`, and the only prior signal was a log line plus one one-shot CEO notification that ages out of the bell. The task row itself kept its healthy status (`in_progress`, `awaiting_qa`, ...), so a wedged task was indistinguishable from a working one anywhere else in the system.

Migration `092_task_stalled_marker` adds two columns to `tasks`, `stalled_reason` and `stalled_since`, so the give-up decision is durable and readable directly off the task row, without reading container logs. A new `GET /api/dashboard/stalled-tasks` endpoint exposes the current stalled set.

This covers the `breaker_tripped` reason only (`_pm_respawn_should_gate`'s strike-cap path). The sibling `notification_cap` reason (the no-`task_id` `_notification_spawn_over_cap` path) is reserved on the `StalledReason` enum but not yet wired; see the parent task `ed14d2e1` for that follow-up.

## Data model

### `roboco.models.base.StalledReason`

```python
class StalledReason(StrEnum):
    BREAKER_TRIPPED = "breaker_tripped"  # _pm_respawn_should_gate strike cap hit
    NOTIFICATION_CAP = "notification_cap"  # reserved: no-task_id spawn-cap path
```

### `tasks` columns (migration `092_task_stalled_marker`)

| Column | Type | Nullable | Meaning |
|---|---|---|---|
| `stalled_reason` | `String(50)` | yes | `NULL` = never stalled, or cleared by genuine forward progress. Otherwise a `StalledReason` value. |
| `stalled_since` | `DateTime(timezone=True)` | yes | When the marker was set. `NULL` when `stalled_reason` is `NULL`. |

`stalled_reason` is a plain string column, not a Postgres enum, so adding `notification_cap` (or any future reason) later needs no `ALTER TYPE` migration. A partial index (`ix_tasks_stalled_reason`, `WHERE stalled_reason IS NOT NULL`) backs the read endpoint's query without indexing the common (not-stalled) case.

Both columns are mirrored on the `Task` pydantic model (`roboco/models/task.py`) and on `TaskResponse` (`roboco/api/schemas/tasks.py`), so any existing task-read API surface picks them up for free.

## Set / clear lifecycle

### Setting the marker: `TaskService.mark_stalled(task_id, reason)`

Called from `_pm_respawn_should_gate`'s tripped block at the exact point it fires `_notify_stuck_agent`, gated by the same in-memory `record["notified"]` one-shot flag the CEO notification already uses:

```python
if not record.get("notified"):
    record["notified"] = True
    self._schedule_respawn_persist(agent_slug, str(task_id), record)
    await self._mark_task_stalled(task_id)  # sets stalled_reason/stalled_since
    await self._notify_stuck_agent(agent_slug, task_id, current_status)
```

`mark_stalled` always (re)stamps `stalled_since` to now, which is correct both for a fresh trip and for a re-trip after a cooldown self-heal attempt that itself failed to make progress (`_pm_cooldown_gate` resets `record["notified"]` after the cooldown window, so the next trip re-notifies and re-stamps). The marker and the CEO notification are one-shot per trip together; a still-wedged task does not double-mark or double-notify between trips.

`_mark_task_stalled` is best-effort: a DB write failure is logged and swallowed so it can never block the dispatch loop or suppress the CEO notification.

### Clearing the marker: `TaskService.clear_stalled_marker(task_id)`

Hooked into `_respawn_status_change_resets`, the same genuine-forward-progress branch that already resets the breaker's in-memory strike counter (`current_status not in seen`, a truly new status, not a revisit of one already seen this run):

```python
if current_status not in seen:
    ...
    self._schedule_respawn_persist(
        agent_slug, str(task_id), self._pm_respawn_tracker[key]
    )
    self._schedule_bg(self._clear_task_stalled_marker(agent_slug, str(task_id)))
    return True
```

The clear is fire-and-forget via `_schedule_bg`, mirroring the counter write-through's discipline: the hot dispatch path never blocks on this DB write. `clear_stalled_marker`'s `UPDATE` is conditioned on `stalled_reason IS NOT NULL`, so the overwhelming majority of calls (a task that was never stalled) are a no-op write. There is no reason-specific clear: any genuine progress clears whatever `stalled_reason` is currently set.

## Read endpoint

### `GET /api/dashboard/stalled-tasks`

Returns the current stalled set, every task with a non-null `stalled_reason`, ordered oldest-stalled-first. The route is a thin handler; all query and duration-classification logic lives in `TaskService.list_stalled_tasks`.

**Response** (`list[StalledTaskResponse]`, `roboco/api/schemas/dashboard.py`):

```json
[
  {
    "task_id": "3f9c1e2a-...",
    "title": "Durable stalled-marker on breaker path + read endpoint",
    "assignee_id": "00000000-0000-0000-0001-000000000001",
    "assignee_slug": "be-dev-1",
    "status": "in_progress",
    "reason": "breaker_tripped",
    "stalled_since": "2026-08-08T18:01:11.808437+00:00",
    "stalled_seconds": 4213.5
  }
]
```

| Field | Notes |
|---|---|
| `task_id`, `title` | Task identity. |
| `assignee_id`, `assignee_slug` | `null` if the task is currently unassigned; `assignee_slug` comes from an outer join against `AgentTable` so an assignee that no longer resolves doesn't drop the row. |
| `status` | The task's current status at read time (e.g. still `in_progress`, the marker does not change the task's own status). |
| `reason` | The `StalledReason` value (currently only ever `breaker_tripped`). |
| `stalled_since` | Timestamp the marker was (most recently) set. |
| `stalled_seconds` | `now - stalled_since` in seconds, computed at query time: "how long" the task has been stalled. |

This endpoint sits on the existing `/api/dashboard` router, which is router-level panel-gated (auth required) like the rest of the dashboard surface; see `roboco/api/routes/dashboard.py`.

## Testing

- `tests/unit/services/test_task_stalled_marker.py`: `TaskService.mark_stalled` / `clear_stalled_marker` / `list_stalled_tasks` in isolation (set, clear-when-set, no-op clear-when-unset, duration computation).
- `tests/unit/runtime/test_stalled_marker.py`: the orchestrator wiring; a breaker trip calls `mark_stalled` alongside `_notify_stuck_agent`, one-shot per trip.
- `tests/unit/runtime/test_pm_respawn_reset.py`: the genuine-forward-progress branch clears the marker via `_schedule_bg`.
- `tests/integration/test_dashboard_routes.py`: `GET /api/dashboard/stalled-tasks` end to end against a real DB.

Together these cover the three acceptance-critical behaviors: a trip sets the marker, genuine progress clears it, and a re-trip after the cooldown window re-marks/re-notifies exactly once rather than double-counting.

## Related

- Parent task `ed14d2e1` ("Backend: durable stalled-task state, notification parity, read endpoint") also covers the `notification_cap` reason on the no-`task_id` `_notification_spawn_over_cap` path; tracked separately, not part of this change.
- `roboco/runtime/orchestrator.py`: `_pm_respawn_should_gate` (breaker trip), `_respawn_status_change_resets` (progress-based reset), `_pm_cooldown_gate` (cooldown self-heal / re-notify eligibility).
