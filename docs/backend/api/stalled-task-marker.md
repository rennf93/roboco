# Durable Stalled-Task Marker + Read Endpoint

## Overview

When the dispatcher's respawn breaker (`_pm_respawn_should_gate` in `roboco/runtime/orchestrator.py`) gives up on a task, the per-agent strike count exceeds `_PM_RESPAWN_MAX_UNPRODUCTIVE`, and the only prior signal was a log line plus one one-shot CEO notification that ages out of the bell. The task row itself kept its healthy status (`in_progress`, `awaiting_qa`, ...), so a wedged task was indistinguishable from a working one anywhere else in the system.

Migration `092_task_stalled_marker` adds two columns to `tasks`, `stalled_reason` and `stalled_since`, so the give-up decision is durable and readable directly off the task row, without reading container logs. A new `GET /api/dashboard/stalled-tasks` endpoint exposes the current stalled set.

This covers the `breaker_tripped` reason only (`_pm_respawn_should_gate`'s strike-cap path). The sibling `_notification_spawn_over_cap` path (no `task_id` to key a marker on) notifies the CEO on trip (see the `_notification_spawn_over_cap` CHANGELOG entry, task 4e7f64c4) but sets no durable task marker — there is nothing to mark. `NOTIFICATION_CAP` remains on the `StalledReason` enum unused, reserved for a future task-keyed variant of that path.

**PR #866 fix-forward (task `cbc0666d`).** The original delivery cleared the marker only from the dispatcher's own re-observation branch (`_respawn_status_change_resets`, see "Clearing the marker" below), which never re-fires once a task's status leaves the dispatcher's fetch scope for that role — e.g. a dev's task advancing from `in_progress` to `awaiting_qa`. A task that recovered from a stall this way kept `stalled_reason` set, and kept appearing in `GET /api/dashboard/stalled-tasks`, until it eventually reached a terminal status. `TaskService._emit_status_transition_audit`, the single chokepoint every status transition funnels through, now clears any stale marker unconditionally on every transition, closing that gap. See the updated "Clearing the marker" and "Testing" sections below for the current (post-fix) lifecycle.

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

### Clearing the marker (post-#866 fix): `TaskService._emit_status_transition_audit`

The **primary** clear path is now unconditional and lives in `TaskService`, not the orchestrator. `_emit_status_transition_audit` is the single chokepoint every task status transition funnels through (it also owns the rework-counter increment and the audit-log write), so it is guaranteed to run on any forward move, not just one observed by the dispatcher's own polling loop:

```python
def _emit_status_transition_audit(self, task, from_status, to_status, ...):
    self._clear_stale_stalled_marker(task)
    ...

@staticmethod
def _clear_stale_stalled_marker(task: TaskTable) -> None:
    if task.stalled_reason is not None:
        task.stalled_reason = None
        task.stalled_since = None
```

This is a direct synchronous attribute assignment (not the async `clear_stalled_marker` helper below) because the caller already holds the ORM object mid-transaction, so the clear commits or rolls back atomically with the transition itself. It was split into its own static method (`_clear_stale_stalled_marker`) to keep `_emit_status_transition_audit`'s own complexity under the xenon budget.

### The dispatcher-side clear: `TaskService.clear_stalled_marker(task_id)` (secondary, same-role path)

Still hooked into `_respawn_status_change_resets`, the dispatcher's genuine-forward-progress branch that also resets the breaker's in-memory strike counter (`current_status not in seen`, a truly new status, not a revisit of one already seen this run):

```python
if current_status not in seen:
    ...
    self._schedule_respawn_persist(
        agent_slug, str(task_id), self._pm_respawn_tracker[key]
    )
    self._schedule_bg(self._clear_task_stalled_marker(agent_slug, str(task_id)))
    return True
```

The clear is fire-and-forget via `_schedule_bg`, mirroring the counter write-through's discipline: the hot dispatch path never blocks on this DB write. `clear_stalled_marker`'s `UPDATE` (used by both paths) is conditioned on `stalled_reason IS NOT NULL`, so the overwhelming majority of calls (a task that was never stalled) are a no-op write. There is no reason-specific clear: any genuine progress clears whatever `stalled_reason` is currently set.

**Why this alone wasn't sufficient (the #866 bug):** this branch only fires when the SAME `(agent, task)` tracker key is re-observed by `_dispatch_dev_work`, which fetches only `pending`/`claimed`/`needs_revision`/`in_progress`/`blocked` tasks. Once a wedged dev's task advances to `awaiting_qa` (or any status outside that dev-role fetch set), that tracker key is never polled again, so this branch alone left a resolved task's marker stuck until the task went terminal. It remains in place as a faster, same-role secondary clear; the `TaskService`-level clear above is what now guarantees correctness for every transition, including cross-role ones.

### The `reassign_active_claim` clear (a PM handing a wedged claim to a new claimant)

`TaskService.reassign_active_claim` (a PM reassigning a claimed/in_progress task's live claim to a different agent, the sanctioned recovery for a wedged dev) never changes `task.status`, so it never routes through `_emit_status_transition_audit`'s unconditional clear above. It now calls `self._clear_stale_stalled_marker(task)` directly alongside its other claim-field writes, so the marker no longer survives the hand-off and misreports a task actively being worked by its new claimant as stalled.

## Read endpoint

### `GET /api/dashboard/stalled-tasks`

Returns the current stalled set, every task with a non-null `stalled_reason`, ordered oldest-stalled-first. The route is a thin handler; all query and duration-classification logic lives in `TaskService.list_stalled_tasks`.

The query excludes terminal (`COMPLETED`/`CANCELLED`) tasks. Since the #866 fix's unconditional `TaskService`-level clear (see "Clearing the marker" above) now guarantees a task's marker is gone by the time it reaches a terminal status through the normal transition chokepoint, this filter is a defensive backstop rather than the primary safeguard — kept in case some future path ever sets a terminal status without going through `_emit_status_transition_audit`.

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

- `tests/unit/services/test_task_stalled_marker.py`: `TaskService.mark_stalled` / `clear_stalled_marker` / `list_stalled_tasks` in isolation (set, clear-when-set, no-op clear-when-unset, duration computation, and `test_list_stalled_tasks_excludes_terminal_statuses_at_query_level` pinning the `status NOT IN ('completed', 'cancelled')` predicate on the compiled SQL), plus two tests added in the #866 fix-forward revision: `test_emit_status_transition_audit_clears_stalled_marker` (a marker is cleared on an arbitrary transition, e.g. `in_progress` -> `awaiting_qa`, regardless of which tracker key drove it) and `test_emit_status_transition_audit_no_op_when_not_stalled` (a task with no marker set is left untouched, no accidental writes).
- `tests/unit/runtime/test_stalled_marker.py`: the orchestrator wiring; a breaker trip calls `mark_stalled` alongside `_notify_stuck_agent`, one-shot per trip.
- `tests/unit/runtime/test_pm_respawn_reset.py`: the dispatcher's same-key genuine-forward-progress branch clears the marker via `_schedule_bg` (the secondary, same-role-recovery path).
- `tests/integration/test_dashboard_routes.py`: `GET /api/dashboard/stalled-tasks` end to end against a real DB.
- `tests/unit/services/test_task.py::test_reassign_active_claim_clears_stalled_marker`: `reassign_active_claim` clears a set `stalled_reason`/`stalled_since` on the new claimant hand-off, even though the call never changes `task.status`.

Together these cover the acceptance-critical behaviors: a trip sets the marker, genuine progress clears it regardless of which path or role observes that progress, a re-trip after the cooldown window re-marks/re-notifies exactly once rather than double-counting, a task that advances past its wedged agent's own dispatch window (the #866 bug) no longer appears in the read endpoint, and a PM reassigning a wedged claim to a new agent also clears the marker even though no status transition occurs.

## Related

- `_notification_spawn_over_cap` (no `task_id` path) notifies the CEO on trip but sets no task marker — there is no task to mark. `NOTIFICATION_CAP` stays unused on `StalledReason`, reserved for a future task-keyed variant.
- `roboco/runtime/orchestrator.py`: `_pm_respawn_should_gate` (breaker trip), `_respawn_status_change_resets` (secondary same-key progress-based reset), `_pm_cooldown_gate` (cooldown self-heal / re-notify eligibility).
- `roboco/services/task.py`: `TaskService._emit_status_transition_audit` / `_clear_stale_stalled_marker` (primary, unconditional clear on any status transition, added in the #866 fix-forward revision, task `cbc0666d`), `TaskService.mark_stalled` / `clear_stalled_marker` / `list_stalled_tasks` / `reassign_active_claim` (calls `_clear_stale_stalled_marker` directly since it changes no status).
- CHANGELOG.md's Unreleased `### Added` section also gained an entry for this feature (durable stalled marker + read endpoint, referencing PR #866) as part of the #866 fix-forward revision — the original delivery's diff had omitted it.
