# Review-path outage detection

Cross-task outage detection for the evidence / git-readonly pipeline (the "review path"). It exists because on Sep 5 `evidence()` and git-readonly reads failed across unrelated tasks and the outage was only found by hand. It is detection only: nothing is remediated automatically beyond clearing the outage state on recovery.

## How it works

`ReviewPathHealthTracker` (`roboco/services/review_path_health.py`) keeps a rolling window of `(timestamp, task_key)` failures.

- **Outage trips** when failures inside the window span at least `review_path_outage_distinct_tasks` **distinct** keys. A single task failing repeatedly counts as one key and never trips it.
- **Outage clears** only on observed successes: `review_path_recovery_successes` consecutive successful reads (evidence legs and 2xx git-readonly reads) clear it and set `recovered=true`. If the window instead empties of failures with no read having succeeded, the outage closes as *expired*: `outage_active` goes false, `recovered` stays false, and the notification says no failures were observed in the window, not that reads are succeeding.
- **Window start** is the timestamp of the oldest failure in the window at the moment the outage trips.
- **State is in memory**, per process. In the split deployment (`ROBOCO_ROLE=api` / `dispatcher`) failures are recorded, and the notification sweep runs, in the **dispatcher** (agents' `ROBOCO_API_URL` points at it; the api role returns before starting background loops). The api-role orchestrator's tracker never sees a failure, so nginx routes `/api/system-health/` to `$active_dispatcher` (`deploy/nginx.conf`, pinned by `tests/unit/test_nginx_routing.py`). A restart forgets the window, which at worst delays a trip by one window.
- **One key per task.** Every git-readonly route (`review_path_key`) and evidence leg (`record_review_path_failure` collapses a full UUID) keys failures by the task's 8-char id prefix, so one task failing across status, log and diff counts once; with no task, a namespaced `branch:`/`project:` key is used.

### Where failures are recorded

| Site | Records |
|---|---|
| `run_bounded_leg` in `roboco/services/gateway/choreographer/evidence_legs.py` | failure on timeouts and other infrastructure-class errors only (NotFound/Validation/Unauthorized, including as a wrapped cause, are caller errors and not recorded; the exception still re-raises), success on a good leg; keyed on the real task id |
| `_translate_read_error` in `roboco/api/utils/git.py` (the five git read routes: status, log, branches, diff, file) | failure on 5xx only (4xx is caller error); the `review_path_read_outcome` route dependency records a success when a read completes without raising; keyed per `resolve_review_path_key` |

Key resolution for git reads (`resolve_review_path_key` in `roboco/services/review_path_health.py`): the `task_id` if the route has one, else the task id parsed from the branch name (last `--` segment), else `branch:<name>`; when the caller names neither, the server-side identity: the calling agent's active task (8-char prefix), else `agent:<id>`. Task-less reads from different agents on different tasks therefore count as distinct keys and can trip a cross-task outage.

### Notifications (existing notification path)

`sweep_review_path_health()` runs from the orchestrator's reaper tick in `roboco/runtime/engines/dispatch_breaker.py`, beside `_maybe_recover_broken_gateway`. It is part of the existing gateway-health recovery subsystem, so it is gated by `ROBOCO_GATEWAY_HEALTH_ENABLED` and is not a separate health mechanism.

The tracker queues each `active` / `recovered` transition exactly once when it happens. The sweep drains the queue and calls `NotificationService.send_review_path_outage_notification`, so there is:

- one `ALERT` (priority HIGH, from `system` to `ceo` and `main-pm`) per outage window, never one per failed call;
- one `ALERT` (priority NORMAL) when reads recover, or a differently worded one ("window closed", no failures observed, recovery not confirmed) when the window expires without a successful read.

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

`tests/unit/services/test_review_path_health.py` covers the boundary (a single task failing repeatedly is not an outage), distinct-task tripping, one event per window, recovery by successes, window expiry (closed without claiming recovery), caller errors ignored by evidence legs, task-less git reads keyed per agent/active task, the missed-sweep case, both hook sites (4xx vs 5xx, key passed), the route contract served through an app client, and a regression where one task failing across status, log, diff and file counts as one key and does not trip the outage. `tests/unit/test_nginx_routing.py` pins the `/api/system-health/` to `$active_dispatcher` routing that the role split depends on.
