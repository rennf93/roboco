# Site 1a round-2: fail_review's developer-notify gains an outer begin_nested() savepoint

Round-1 pr_gate finding F-71881265 on the branch that shipped
`docs/backend/qa/fail-review-notify-failure-safe.md` (site 1a). That fix
guarded `a2a.send()` **raising**; it did not guard a **mid-flush failure**
*inside* the send — a distinct failure mode that leaves the shared request
session rollback-only even though the exception is still caught. This task
closes that gap by adding the outer `begin_nested()` savepoint site 1d
(`doc.py`'s `_handoff_to_cell_pm` caller) already had, so site 1a now
matches site 1d's shape exactly.

## The gap the round-1 fix left open

`_fail_review_developer_notify` (`qa.py:1063`) already wrapped its own
`a2a.send()` call in a narrow `try/except Exception`, but that inner catch
only stops a **raise** from propagating. A flush failure that happens
*inside* `a2a.send()`'s own DB writes (a conversation/participant insert,
for example) can leave the session's transaction in a rollback-only state
even after the exception is caught — the raise still happens and is still
caught, but the shared `self.task.session` is now poisoned. `fail_review`'s
caller reuses that same session for the response commit right after this
verb returns; a poisoned session there loses the `needs_revision`
transition and the inserted findings rows the reviewer believes already
landed, silently.

## What shipped

`fail_review` now wraps the entire `await self._fail_review_developer_notify(...)`
call in `async with self.task.session.begin_nested():` (`qa.py:1199`),
mirroring `doc.py:590-609`'s `_finalize_documented` savepoint shape exactly:

- On any exception inside the savepoint block — including a flush failure
  the inner catch didn't stop, since `begin_nested()`'s own context-manager
  exit re-raises past the inner `try/except Exception` — SQLAlchemy rolls
  back to the savepoint instead of poisoning the whole transaction.
- The outer `except Exception` then calls `await self.task.session.refresh(t)`
  **before** the return path reads `t.status` / calls `with_introspection`.
  This ordering is load-bearing: a savepoint rollback expires every
  attribute SQLAlchemy touched on `t` inside the block, so reading
  `t.status` first raises `MissingGreenlet` (not a friendly `AttributeError`)
  and propagates uncaught, rolling back the *whole request* — the exact
  failure mode this fix exists to prevent, now triggered by the fix's own
  except path if the ordering were wrong.
- Both the inner send-level catch and the new outer savepoint-level catch
  render the identical warning string via a new shared static helper,
  `_fail_review_notify_failure_warning(exc)`, so a reviewer sees the same
  message regardless of which layer caught the failure.

The prior warning string, the `f"QA needs changes.\n{summary}"` body, the
`t.assigned_to is not None` gate, and `_teardown_sandbox_best_effort`
running unconditionally after the notify attempt are all unchanged.

## Tests

`tests/unit/gateway/test_choreographer_qa.py` gained
`test_fail_review_survives_savepoint_flush_failure`: a fake session whose
`flush()` raises on its *third* call (after the findings-ledger insert and
`VerbRunner`'s own savepoint around the composed transition both succeed)
so the failure lands specifically at the notify savepoint's own release —
not at `a2a.send()` itself raising, which the round-1 test already covered.
The test asserts the envelope still comes back `ok` with `status ==
"needs_revision"`, `task_svc.qa_fail` and `a2a_svc.send` were both awaited
exactly once, `session.refresh` was awaited once (proving the
refresh-before-read ordering ran), and the warning text names the
`RuntimeError` — proving both the envelope and the earlier transition
commit survive a failure class the round-1 fix didn't reach.

## Scope note

Confined to `roboco/services/gateway/choreographer/qa.py` and its tests,
per the task's constraints — `_impl.py` (site 1b/1c, a parallel subtask in
the same series) and `doc.py` (already carries this shape) were untouched.
