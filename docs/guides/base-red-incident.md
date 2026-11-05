# Base-red incident service

When a project's integration base branch has red CI, `BaseRedIncidentService` (`roboco/services/base_red_incident.py`) opens exactly one incident with one named owner, so cells stop each rediscovering the same failure. It closes the incident on the first green reading. This is detection and ownership only; refusing review submission while an incident is open is a separate gate that consumes the interface below.

## How it runs

There is no new scheduler or HTTP client. `CiWatchEngine.run_cycle` already fetches CI readings (`TelemetrySample`s) through the existing `GitService` CI plumbing; after originating fix tasks it calls `BaseRedIncidentService.observe(samples, projects)` with the same samples. The incident check therefore runs only while CI-watch is armed (`ROBOCO_CI_WATCH_ENABLED` plus per-project `ci_watch_enabled`), on the CI-watch interval.

## Lifecycle

State lives in `base_red_incidents` (migration `112_base_red_incidents`, model `roboco/models/base_red_incident.py`). A partial unique index on `(project_id, base_branch)` where `status IN ('watching','open')` guarantees at most one live row per project and base.

| Reading | No live row | `watching` row | `open` row |
|---|---|---|---|
| Red | create `watching`, count 1 | count +1; at the threshold promote to `open` | count +1 |
| Green | nothing | row deleted (a transient red, never an incident) | close, reason `base CI green`, `cleared_by=system` |
| Pending / unknown / lookup error | nothing | nothing | nothing |

The threshold is `ROBOCO_BASE_RED_CONSECUTIVE_THRESHOLD` (default 2). Pending, no-signal and lookup-error readings are dropped upstream by the telemetry source, so they are *unknown*: they never count as red and never close an incident. Each project is applied in its own savepoint with a broad catch, so one project's failure is logged and skipped; the cycle and any gate fail open.

## Ownership

On promotion to `open` the service picks one owner task: it adopts the repo's open CI-watch fix task if there is one, otherwise it creates a P0 planning task owned by Main PM (`main-pm`, source `ci_watch`). `owner_task_id` and `owner_agent` (the task assignee's slug, falling back to `main-pm`) are recorded on the incident, and the owner receives an ack-required notification naming the task. The notification is best-effort; a send failure is logged and does not undo the incident.

## Public interface

```python
svc = get_base_red_incident_service(session)
await svc.get_open_incident(project_id) -> BaseRedIncident | None  # status=open only
await svc.is_owner_task(task_id) -> bool                           # task owns an open incident
await svc.clear(project_id, cleared_by: str, reason: str) -> BaseRedIncident | None
```

`get_open_incident` exposes `owner_task_id`, `owner_agent`, `base_branch` and `opened_at`; a `watching` row is not an incident and is never returned. `clear` is the manual path: it closes the open incident with the given reason, records `cleared_by`, and returns the closed incident, or `None` when none is open. The service flushes only; the caller commits.

## Audit

Three audit events are written, best-effort: `base_red.opened`, `base_red.closed` (automatic, green) and `base_red.cleared` (manual). Details carry `project_id`, `base_branch`, `reason`, `cleared_by` and `owner_agent`. The audit service nulls non-UUID agent ids, which is why the actor slug travels in `details` rather than as the agent id.

## Not included

No HTTP route exposes `clear` yet; callers use the service directly. The submission gate (`i_am_done` / PR submit refusing while an incident is open, with the owner's own task exempt) is a separate task built on `get_open_incident` and `is_owner_task`.
