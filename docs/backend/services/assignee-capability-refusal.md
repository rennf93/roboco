# Assignee-capability refusal on assignment paths

A task must never be assigned to an agent whose role cannot act on the status the task will hold. Two live wedges had this shape: task 54e30535 sat assigned to a product-owner id that has no delivery verbs, so nobody could act on it, and 4be7d6d3 was restored to a status its assignee could not drive. This change closes the **assignment** half: `delegate`, `reassign` and the admin `PATCH assigned_to` path now refuse such an assignment at the moment it is made, and the refusal names the missing capability. The restore-path half (unblock restore, escalation divert) is a separate leaf; it should reuse the same predicate.

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

## Behavior notes

- Capability is derived, so a change to `CLAIM_RULES` or the action specs changes what is refused with no second table to update.
- A same-value assignment is never refused, so re-PATCHing a task with its existing assignee still works even if that assignee is already incoherent. Fixing such a task means assigning a role that can act on it.
- DevOps and Board roles are judged by the same tables: a Board role has an empty `CLAIM_RULES` entry, so it is refused on any status where no action lists it.

## Tests

- `tests/unit/foundation/policy/test_assignee_capability.py`: the pure predicate.
- `tests/unit/services/test_assignee_capability_guard.py`: the service guard on the delegate, reassign and admin-set paths.
