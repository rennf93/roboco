# Coordination-Event Notifications

The NotificationService provides five typed producers for coordination events—state transitions that affect multiple agents across a task lifecycle. Each producer fires at a specific chokepoint and is guarded against double-firing through idempotent upstream conditions.

## Overview

Coordination events differ from generic task-state notifications: they signal changes that require coordination between agents (reassignments, dependency unblocks, sequencing blocks) or escalations (stale claims). Every producer follows the same pattern:

1. **Typed producer method** in `NotificationService` (e.g., `send_reassignment_notification`)
2. **Best-effort wiring** at the chokepoint via a helper method (e.g., `_notify_reassignment` in TaskService)
3. **Try/except guard** — a notification failure never breaks the underlying state transition
4. **Double-fire prevention** — idempotent upstream guards ensure no duplicate notifications

## The Five Producers

### 1. Reassignment: `send_reassignment_notification`

**Fired from:** `TaskService.reassign()` → `_notify_reassignment()`

**Trigger:** A task is reassigned to a different agent.

**Recipients:** Previous assignee + new assignee + CEO

**Double-fire guard:** `_notify_reassignment` checks `new_assignee == previous_assignee` and returns early if nothing changed. TaskService only captures the assignee once (pre-mutation) so if `reassign` is called twice in succession, the second call operates on an already-updated assignee and the guard catches it.

**Subject & body:**
```
Subject: Task {task_id} reassigned
Body: Task {task_id} was reassigned from {previous} to {new}.
```

**Example scenario:** A PM delegates a task from developer A to developer B. Both developers and the CEO receive notification of the change.

---

### 2. Collision Sequencing: `send_collision_sequencing_notification`

**Fired from:** `TaskService.wire_sibling_collision_dag()` → `_notify_collision_sequencing()`

**Trigger:** A file/migration/shared-surface collision causes the collision-sequencing analyzer to add a dependency edge, holding back one task behind a blocking sibling.

**Recipients:** Held-back task's assignee + CEO

**Double-fire guard:** The wiring only fires the notification when `add_dependency` returns `True` (a freshly-inserted edge). Subsequent calls to `wire_sibling_collision_dag` over the same sibling pair contribute no new edge and therefore trigger no notification.

**Subject & body:**
```
Subject: Task {held_back_task_id} sequenced behind a sibling
Body: Task {held_back_task_id} was held back by the collision-sequencing 
       analyzer: it now depends on task {blocking_task_id}, which surfaced 
       an overlapping file/migration/shared-surface collision. It will 
       resume once that task reaches a terminal state.
```

**Example scenario:** Two backend dev tasks both touch `roboco/models/task.py` and one adds a migration. The analyzer detects a collision and adds a sequencing edge, notifying the developer of the held-back task.

---

### 3. Unblock: `send_unblock_notification`

**Fired from:** `TaskService.unblock()` / `unblock_with_restore()` → `_notify_unblock()`

**Trigger:** A PM or resolver explicitly unblocks a task that was in BLOCKED status.

**Recipients:** Restored owner + CEO

**Double-fire guard:** Both `unblock` and `unblock_with_restore` are guarded by a status check (`status != BLOCKED` short-circuits early). A repeated call against an already-unblocked task is a no-op upstream and never reaches the notification handler.

**Subject & body:**
```
Subject: Task {task_id} unblocked
Body: Task {task_id} has been unblocked and handed back to {owner}. 
       It is ready to resume.
```

**Example scenario:** A task was blocked by an external dependency. The dependency resolves, the PM calls `unblock()`, and the task owner is notified it's ready to resume.

---

### 4. Dependency Revival: `send_dependency_revival_notification`

**Fired from:** `TaskService._unblock_dependents()` → `_notify_dependency_revival()`

**Trigger:** A task's **last outstanding dependency completes**, automatically reviving the task.

**Recipients:** Revived task's assignee + CEO

**Double-fire guard:** `_unblock_dependents` prunes the `dependency_ids` list **before** firing the notification. A repeated call for the same completed dependency finds no matching dependent and never reaches the notification handler.

**Distinct from `send_unblock_notification`:** This fires when a dependency completes automatically (no resolver acted). `send_unblock_notification` fires when a PM explicitly calls unblock on a task blocked by escalation. The notification names which dependency unblocked it rather than who resolved it.

**Subject & body:**
```
Subject: Task {task_id} revived by dependency completion
Body: Task {task_id} was revived: its dependency {completed_dependency_id} 
       just completed and no other dependencies remain. It is ready to resume.
```

**Example scenario:** A task was blocked waiting on three dependencies. The first two complete and unblock nothing (others remain). The third completes, the last dependency clears, and the task is auto-revived with a notification.

---

### 5. Stale Claim Reaped: `send_stale_claim_reaped_notification`

**Fired from:** Orchestrator's `_reap_with_service()` → `_notify_stale_claim_reaped()`

**Trigger:** The reaper detects a stale claim (no heartbeat updates) and releases it back to PENDING.

**Recipients:** Reaped agent + CEO

**Priority:** HIGH (higher than other coordination events; stale claims are operational issues)

**Double-fire guard:** A reaped task leaves `list_in_progress_or_claimed` once released to PENDING. A subsequent reaper tick never re-considers the same claim and cannot re-fire the notification.

**Subject & body:**
```
Subject: Task {task_id}: stale claim reaped
Body: Task {task_id}'s claim went stale (last heartbeat: {timestamp}) 
       and was reaped back to pending, releasing it from {reaped_agent}.
```

**Example scenario:** An agent crashed or became unresponsive while holding a task claim. The reaper detects the stale heartbeat, releases the claim, and notifies both the agent and CEO of the forced release.

---

## Implementation Pattern

Every producer follows a consistent best-effort pattern at its call site:

```python
async def _notify_<event>(self, ...) -> None:
    """Best-effort coordination notification for a <event>."""
    if <guard condition>:
        return
    try:
        from roboco.services.notification import NotificationService
        await NotificationService().send_<event>_notification(...)
    except Exception as e:
        self.log.warning(
            "<Event> notify failed", task_id=str(...), error=str(e)
        )
```

**Why this pattern:**
- **Localized guards** in the helper prevent unnecessary notification attempts
- **Try/except** ensures a notification failure never breaks the state transition
- **Logged & swallowed** — operational visibility without crashing the flow
- **Lazy import** avoids circular dependencies at the service layer

## Adding a New Coordination Event

When a new coordination event arises:

1. **Add a new producer method** to `NotificationService` following the existing signature (typed params, docstring describing fire condition + double-fire guard, calls `_create_notification` with `related_task_id` set)
2. **Add a helper** in the originating service (TaskService, Orchestrator, etc.) following the best-effort pattern
3. **Wire at the chokepoint** — the single place the state transition happens
4. **Document the guard** in the helper's docstring so reviewers understand why no duplicate can fire
5. **Test the guard** — add a chokepoint-level test proving a repeated call doesn't fire twice

See `test_notification.py` and `test_task.py` for worked examples of producer-level and chokepoint-level tests.

## Related Files

- **Implementation:** `roboco/services/notification.py` (producers)
- **Wiring:** `roboco/services/task.py` (TaskService helpers), `roboco/runtime/orchestrator.py` (reaper hook)
- **Tests:** `tests/unit/services/test_notification.py` (producer unit tests), `tests/unit/services/test_task.py` (chokepoint double-fire proofs)
- **Data model:** `roboco/models/notification.py` (CreateNotificationParams)
