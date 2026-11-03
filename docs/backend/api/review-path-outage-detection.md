# Review-path outage detection

Cross-task outage detection for the evidence / git-readonly pipeline (the "review path"). It exists because on Sep 5 `evidence()` and git-readonly reads failed across unrelated tasks and the outage was only found by hand. It is detection only: nothing is remediated automatically beyond clearing the outage state on recovery.

## How it works

`ReviewPathHealthTracker` (`roboco/services/review_path_health.py`) keeps a rolling window of `(timestamp, task_key)` failures.

- **Outage trips** when failures inside the window span at least `review_path_outage_distinct_tasks` **distinct** keys. A single task failing repeatedly counts as one key and never trips it.
- **Outage clears** when `review_path_recovery_successes` consecutive successful reads arrive, or when the window empties of failures.
- **Window start** is the timestamp of the oldest failure in the window at the moment the outage trips.
- **State is in memory**, per process. In the split deployment (`ROBOCO_ROLE=api` / `dispatcher`) failures are recorded, and the notification sweep runs, in the **dispatcher** (agents' `ROBOCO_API_URL` points at it; the api role returns before starting background loops). The api-role orchestrator's tracker never sees a failure, so nginx routes `/api/system-health/` to `$active_dispatcher` (`deploy/nginx.conf`, pinned by `tests/unit/test_nginx_routing.py`). A restart forgets the window, which at worst delays a trip by one window.
- **One key per task.** Every git-readonly route (`review_path_key`) and evidence leg (`record_review_path_failure` collapses a full UUID) keys failures by the task's 8-char id prefix, so one task failing across status, log and diff counts once; with no task, a namespaced `branch:`/`project:` key is used.

### Where failures are recorded

| Site | Records |
|---|---|
| `run_bounded_leg` in `roboco/services/gateway/choreographer/evidence_legs.py` | failure on error (then re-raises), success on a good leg; keyed on the real task id |
| `_translate_read_error` in `roboco/api/utils/git.py` (the five git read routes: status, log, branches, diff, file) | failure on 5xx only (4xx is caller error, not an outage); keyed on task id, else branch, else `project_slug` |

Key resolution for git reads (`review_path_key`): the `task_id` if the route has one, else the task id parsed from the branch name (last `--` segment), else `branch:<name>`, else `project:<slug>`. A git-only failure with no task or branch context therefore collapses to the single `project:<slug>` key and will not trip on its own; evidence legs and task-scoped git reads key on real task ids.

### Notifications (existing notification path)

`sweep_review_path_health()` runs from the orchestrator's reaper tick in `roboco/runtime/engines/dispatch_breaker.py`, beside `_maybe_recover_broken_gateway`. It is part of the existing gateway-health recovery subsystem, so it is gated by `ROBOCO_GATEWAY_HEALTH_ENABLED` and is not a separate health mechanism.

The tracker queues each `active` / `recovered` transition exactly once when it happens. The sweep drains the queue and calls `NotificationService.send_review_path_outage_notification`, so there is:

- one `ALERT` (priority HIGH, from `system` to `ceo` and `main-pm`) per outage window, never one per failed call;
- one `ALERT` (priority NORMAL) when reads recover.

A trip-and-clear between two sweeps still emits both alerts. A failed send is logged and does not break the tick.

## Configuration

All settings are in `roboco/config.py` (env prefix `ROBOCO_`).

| Setting / env var | Default | Constraint | Meaning |
|---|---|---|---|
| `review_path_outage_window_seconds` / `ROBOCO_REVIEW_PATH_OUTAGE_WINDOW_SECONDS` | `300` | >= 1 | Rolling window length |
| `review_path_outage_distinct_tasks` / `ROBOCO_REVIEW_PATH_OUTAGE_DISTINCT_TASKS` | `3` | >= 2 | Distinct tasks that must fail inside the window to declare an outage |
| `review_path_recovery_successes` / `ROBOCO_REVIEW_PATH_RECOVERY_SUCCESSES` | `3` | >= 1 | Consecutive successful reads that clear an active outage |

## Panel health surface

`GET /api/system-health/review-path` (route in `roboco/api/routes/health.py`, schema `ReviewPathHealthResponse` in `roboco/api/schemas/health.py`). The route is thin: it returns the tracker's `state()`.

This is the frozen cross-cell contract with the frontend banner slice.

| Field | Type | Meaning |
|---|---|---|
| `outage_active` | bool | An outage is currently active |
| `type` | string | Always `"review_path_tools"` |
| `window_start` | timestamp or null | When the active (or last) outage window began; null if none has occurred |
| `recovered` | bool | True after an outage has cleared; reset to false when a new outage trips |

Example while an outage is active:

```json
{
  "outage_active": true,
  "type": "review_path_tools",
  "window_start": "2026-11-03T05:10:42.118000Z",
  "recovered": false
}
```

## Tests

`tests/unit/services/test_review_path_health.py` covers the boundary (a single task failing repeatedly is not an outage), distinct-task tripping, one event per window, recovery by successes and by window expiry, the missed-sweep case, both hook sites (4xx vs 5xx, key passed), the route contract served through an app client, and a regression where one task failing across status, log, diff and file counts as one key and does not trip the outage. `tests/unit/test_nginx_routing.py` pins the `/api/system-health/` to `$active_dispatcher` routing that the role split depends on.
