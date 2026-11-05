# Task-scoped wedge ledger

The wedge ledger (`roboco/services/wedge_ledger.py`, PR #1127) generalizes the #685 oscillation breaker. The #685 breaker only counts strikes at the gateway's `unblock` verb. A task can therefore loop through a mix of verbs and actors and never accrue a strike. The September loop did exactly that: `unblock` succeeded, the next verb bounced `not_authorized`, an escalation blocked the task, the pool reclaimed it, and the cycle respawned. The ledger counts **status re-arrivals**, not verbs, so the same loop is caught.

## How a strike is counted

`TaskService._emit_status_transition_audit` is the single transition chokepoint. After every status transition it calls `_maybe_schedule_wedge_ledger_check` **exactly once** (an earlier revision called it twice, which double-counted every strike). That method registers `run_wedge_ledger_check(task_id, to_status)` with `defer_after_commit(self.session, ...)`, the transactional outbox, so the check runs only **after the caller's transaction commits** and is dropped if it rolls back. The check still uses a **fresh DB session**, but because it runs post-commit it reads committed status and markers. It can no longer force `BLOCKED` before the caller's own status lands, and the caller's marker flush can no longer overwrite its ledger write. A failure in the check is logged and never fails the transition.

`WedgeLedgerService.check_transition` then:

1. Skips arrivals at `blocked`, `completed` and `cancelled`. The trip itself transitions into `blocked`, so counting it would recurse. A terminal task is done whatever its ledger says.
2. Computes the **#685 progress fingerprint**, `[commit_count, revision_count, terminal_children]`, via `progress_fingerprint`. This is the same triple the #685 breaker uses. The terminal-children count comes from `TaskService.terminal_children_count`, so that component has exactly one definition.
3. Folds the arrival into the ledger with the pure function `evaluate_arrival`.

`evaluate_arrival` rules:

- **Reset.** If a prior fingerprint exists and differs from the current one, any of the three signals moved. Every strike counter clears, `tripped` resets to `False`, and `last_progress_at` is stamped with the current time.
- **Strike.** Otherwise, meaning the first-ever fingerprint or an unchanged one, one strike is added to the counter for `to_status`.

Counters are **keyed by status only**. No actor or verb enters the count, so any mix of actors cycling through the same status accrues together.

## Threshold and trip

The threshold is `Settings.wedge_ledger_threshold` (`ROBOCO_WEDGE_LEDGER_THRESHOLD`, integer, minimum 1, **default 5**). The default matches the gateway's `_OSCILLATION_TRIP_THRESHOLD`.

The ledger trips when the highest per-status strike count **exceeds** N, so with the default it blocks on the 6th strike, not the 5th. This matches the #685 breaker's "trip above N" behavior, and the config description says so.

On a trip, `WedgeLedgerService._trip`:

1. Builds the cycle from the audit log (see below).
2. Sets `tripped`, `tripped_at` and `tripped_strikes` on the ledger marker.
3. Sets `blocker_resolver_type = HUMAN`.
4. Moves the task to `BLOCKED` through `TaskService.admin_set_status(..., actor_role="system")`.
5. Calls `NotificationService.send_wedge_blocked_notification` to alert the CEO.

If the notification fails, the failure is logged and the block stands.

### CEO notification

The notification is a `BLOCKER_ESCALATION` with `HIGH` priority from `system` to `ceo`. Its subject reads `Task <title> wedged (Nx no-progress cycle)`. The body names the status and strike count, then lists one line per transition:

```
<timestamp>  <actor> (<role>)  [<event>]  <from_status> -> <to_status>
```

`<event>` is the audit event name for that transition (for example `task.pr_fail` or `task.request_changes`), which serves as the verb proxy, so the CEO can see which verb drove each leg. The body's lead-in reads "Full cycle (oldest first):".

The cycle is rebuilt from the task's last 20 `task.*` audit rows (`_CYCLE_LIMIT`). `_transition_cycle` reverses the descending query, so lines run oldest first within that window. The audit journey stays the source of truth and the ledger never duplicates it.

## Storage

The ledger is a task marker named `wedge_ledger`, accessed through `get_wedge_ledger`, `set_wedge_ledger` and `clear_wedge_ledger` in `roboco/foundation/policy/content/markers.py`. Those helpers are storage only. All logic and DB writes stay in `roboco/services/wedge_ledger.py`. No route was added or changed.

Payload:

```json
{"progress_fp": [0, 0, 0], "strikes": {"needs_revision": 2}, "tripped": false,
 "tripped_at": "<iso, only once tripped>", "tripped_strikes": 0,
 "last_progress_at": "<iso>"}
```

## Reader surface

- `get_wedge_state(task)` returns the marker with guaranteed keys: `strikes` is always a dict and `tripped` always a bool. It also returns `progress_fp`, `tripped_at`, `tripped_strikes` and `last_progress_at`.
- `WedgeLedgerService.wedge_cycle(task_id)` returns the rebuilt cycle for other surfaces, such as the stuck-state task payload, without re-deriving the audit query.
- `last_progress_at` is stamped on each fingerprint movement, so readers can compute time since progress without replaying the audit log.

## Tests

`tests/unit/services/test_wedge_ledger.py` covers the strike and reset rules and the September mixed-verb loop shape: unblock succeeds, the next verb is `not_authorized`, an escalate bounce, then a pool reclaim. It also tests that mixing actors across a same-status cycle still accumulates strikes. The notification test asserts the "oldest first" label and that every cycle line carries its event. `tests/unit/services/test_task.py` runs the real `_emit_status_transition_audit` and asserts exactly one deferred wedge check is registered per transition, that it does not run before commit, and that the deferred work awaits `run_wedge_ledger_check` with the task id and target status. The existing no-background-task assertion is now strict, since the check is deferred rather than spawned.
