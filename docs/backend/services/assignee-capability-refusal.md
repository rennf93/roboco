# Assignee-capability refusal on assignment paths

A task must never be assigned to an agent whose role cannot act on the status the task will hold. Two live wedges had this shape: task 54e30535 sat assigned to a product-owner id that has no delivery verbs, so nobody could act on it, and 4be7d6d3 was restored to a status its assignee could not drive. This change closes the **assignment** half: `delegate`, `reassign` and the admin `PATCH assigned_to` path now refuse such an assignment at the moment it is made, and the refusal names the missing capability. The restore-path half (unblock restore, escalation divert) reuses the same predicate; see "Restore-path divert" below.

## The predicate

`roboco/foundation/policy/lifecycle.py` owns it, next to the role-transition tables it reads. There is no parallel capability map.

- `assignee_can_act(role, status) -> bool` is true when the role has at least one way to act on a task in `status`:
  - the status is in the role's `CLAIM_RULES` entry (the claim path), or
  - some atomic action in `_ATOMIC_ACTIONS` lists the role in `allowed_roles` and the status in `source_statuses` (the act-in-place path: `complete` from `awaiting_pm_review`, `ceo_approve` from `awaiting_ceo_approval`, `resume` from `paused`, and so on).
- `completed` and `cancelled` are exempt: nothing is left to act on, so any assignee is coherent.
- The `claim` and `cancel` actions are skipped in the action scan. `claim`'s `allowed_roles` / `source_statuses` are the union across roles (the per-role narrowing is `CLAIM_RULES`, already consulted). `cancel` is a near-universal escape hatch for PMs and the CEO, so scanning it would make almost any task "actable" for its cancellers, which is not delivery capability.
- `assignee_capability_gap(role, status) -> str | None` returns the refusal text, or `None` when the role can act. The text reads `role '<role>' has no action on status '<status>' — it cannot claim or act on a task in that state; assign a role with a claim/action edge on '<status>'`.

## Where it is enforced

The check lives in `TaskService._refuse_incapable_assignee(status, new_assignee, current=None)` (`roboco/services/task.py`), which raises `AssigneeCapabilityError` (`roboco/services/base.py`, a `ServiceError` carrying `role` and `status`) with the message prefix `ASSIGNEE_INCAPABLE:`. It passes through, without refusing:

- a `None` assignee (a clear),
- a same-value set (`new_assignee == current`),
- an assignee UUID that is not a seeded agent (`role_for_uuid_or_none` returns `None`); the existing identity guards own that case.

It is called from four sites in `TaskService`:

| Path | Site | Status checked |
|---|---|---|
| `delegate` | `create_subtask`, before the row exists | the status the subtask will hold (`prepared.status`; PENDING when assigned) |
| `reassign` | after the existing board / main-pm diversions, on the effective assignee | the task's current status |
| `reassign_active_claim` | same | the task's current status |
| admin `PATCH assigned_to` | `update`, before the field loop, so a refusal never leaves the session dirty | the task's current status |

## What the caller sees

The error category is `not_authorized`, with the capability named in the message.

| Surface | Mapping |
|---|---|
| `delegate` verb (`choreographer/pm.py`) | `Envelope.not_authorized`, `remediate`: re-issue `delegate(...)` with an assignee whose role can claim or act on the subtask's target status |
| `reassign` verb (`choreographer/_impl.py`, `_apply_reassign`) | `Envelope.not_authorized`, `remediate`: reassign to an agent whose role can act on the task's current status |
| `PATCH` task route (`_update_or_translate` in `roboco/api/routes/tasks.py`) | HTTP 403 via `_translate_error` in `roboco/api/utils/tasks.py` |

Routes and handlers only translate the error; the decision and the DB writes stay in the service layer.

## Restore-path divert

Refusing at assignment time is not enough: `unblock` with restore puts a task back into its pre-block status for its pre-block owner, and that pair can be incoherent. Task 4be7d6d3 was restored to `awaiting_pm_review` for an owner whose role has no `complete` verb; `next` named `complete()`, and `complete()` answered `not_authorized` ("not assigned to you"). Restore now consults the same predicate instead of restoring into a claimable-but-unactable state.

`TaskService._divert_incapable_restore(task, restored_status)` (`roboco/services/task.py`) runs in `unblock_with_restore` just before `_apply_pre_block_restore`:

1. It resolves the role of the snapshotted owner (`task.pre_block_assignee`) via `role_for_uuid_or_none`. No owner, or an unseeded UUID, means no divert (same pass-through as the assignment guard).
2. It calls `lifecycle.assignee_capability_gap(role, restored_status)`. `None` means the owner can act and the restore proceeds unchanged.
3. Otherwise it reuses `_divert_owned_task_to_pool`, the pool release Board-role assignments already get, appends a `[RESTORE REDIRECTED]` note to `dev_notes` naming the role, the status and the gap, then clears the claim and the block snapshot. The task lands in `pending` unassigned for a role-matched reclaim, and `unblock_with_restore` returns it without notifying the old owner.

No new capability map or actor-set helper was added.

**Caveat:** a diverted task re-enters as `pending`, not its pre-block status (for example `awaiting_pm_review`). This matches the existing Board divert. The review-queue position is lost, and the next claimant picks the work up from the pool.

### Escalation divert

Escalation divert does **not** share the restore path. `apply_escalation` decides with its own target-based check (`_escalation_diverts_to_pool`) on the escalation target. The escalation target never becomes the restored owner, because the block snapshot keeps the pre-escalation assignee. So a later `unblock` of an escalated task goes through `_divert_incapable_restore` against that snapshotted owner, and both paths are covered without a second capability check.

## Behavior notes

- Capability is derived, so a change to `CLAIM_RULES` or the action specs changes what is refused with no second table to update.
- A same-value assignment is never refused, so re-PATCHing a task with its existing assignee still works even if that assignee is already incoherent. Fixing such a task means assigning a role that can act on it.
- DevOps and Board roles are judged by the same tables: a Board role has an empty `CLAIM_RULES` entry, so it is refused on any status where no action lists it.

## Tests

- `tests/unit/foundation/policy/test_assignee_capability.py`: the pure predicate.
- `tests/unit/services/test_assignee_capability_guard.py`: the service guard on the delegate, reassign and admin-set paths.
- `tests/unit/services/test_restore_capability_divert.py`: an incapable owner's restore diverts to the pool (status `pending`, unassigned, snapshot cleared, `[RESTORE REDIRECTED]` note); a capable owner's restore is unchanged.
- `tests/unit/services/test_assignee_capability_wedge_regressions.py`: incident-named regressions over the real service methods. For 4be7d6d3 it checks that restoring `awaiting_pm_review` to a developer or QA owner, and `awaiting_qa` to a developer, diverts to `pending` unassigned with the snapshot cleared, a `[RESTORE REDIRECTED]` note and no unblock notification. A control case checks that a `main-pm` restore to `awaiting_pm_review` is unchanged. For 54e30535 it checks that admin set (`update`), `reassign` and `create_subtask` (delegate) each raise `AssigneeCapabilityError` naming the role and status with no partial write, and that delegate refuses before `create` is called. The reassign test stubs the board-advisory and main-PM divert helpers and the cell-PM redirect so the capability backstop is reached. A Board-role target would divert instead of being refused, so the test uses the cross-lane dev/QA roles.
