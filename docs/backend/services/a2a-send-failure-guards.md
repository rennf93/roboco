# A2A-Send Failure Guards in the Gateway Choreographer

Four post-transition handoff sites in the gateway choreographer
(`roboco/services/gateway/choreographer/`) deliver a notification via
`await self.a2a.send(...)` **after** the verb's real transition has already
been composed — and, on some paths, committed. `A2AService.send` is **not**
best-effort: it raises on policy denial (`A2AAccessDeniedError`,
`roboco/services/a2a.py:1816`), a missing-agent lookup (`a2a.py:1792`),
conversation/participant validation (`a2a.py:1251/1272/1275`), and
transient DB errors mid-flush. Left unguarded, a raise here escapes the
verb body after the real work already happened: the caller gets an opaque
verb error, sandbox teardown can be skipped, and — because the raise can
poison the shared session before the root commit — the whole transaction
(the transition, `task_review_findings` rows, the structured note) can
roll back together, silently undoing work the caller believes it already
delivered.

## The guard shape (the in-repo template)

`_pass_review_documenter_handoff` (`roboco/services/gateway/choreographer/qa.py:962-994`)
is the canonical pattern every site below copies verbatim:

```python
try:
    await self.task.reassign(task_id, recipient_id)
    await self.a2a.send(...)
except Exception as exc:
    logger.warning(
        "<verb> side-effect failed - transition committed, handoff did not fire",
        task_id=str(task_id),
        recipient=str(recipient_id),
        error=repr(exc),
    )
    return f"<Transition-name> committed but the handoff to {recipient_id} failed ({exc!r}). Re-issue via dm."
return None
```

The helper catches `Exception` broadly — not just `A2AAccessDeniedError` —
because reassignment and the send can each fail for unrelated reasons, and
degrades to a **warning string** rather than letting anything escape. The
caller threads that warning into the otherwise-unchanged success envelope
(`Envelope.ok(..., warning=warning)`); nothing about the response status
changes on a handoff failure. The warning always carries the recipient id
and `repr(exc)` (not `str(exc)`) so the exception type is visible, not just
its message — enough to debug without re-deriving from logs alone.

## The four sites (task 41b4459e, "guard the four post-transition a2a.send sites")

| Site | Verb | File | Landed as |
|---|---|---|---|
| 1a | `fail_review` → original developer | `qa.py:1124-1131` | task `0664b042` |
| 1b | `request_changes` → revision owner | `_impl.py:8751-8758` | sibling leaf under 41b4459e |
| 1c | submit-qa QA handoff (`_notify_qa`) | `_impl.py:3941-3958` | sibling leaf under 41b4459e |
| 1d | `i_documented` PM handoff (`_handoff_to_cell_pm`) | `doc.py:658-676` | task `ee084ee2` |

Site 1d (`_handoff_to_cell_pm`, `doc.py`) is the one this note describes in
detail — it is also `i_documented`'s only post-transition side effect, so
its docstring explicitly notes the `docs_complete` transition is already
committed by the time the helper runs. The caller (`_finalize_documented`)
keeps its own pre-existing outer `session.begin_nested()` + `except`
backstop unchanged (refreshing `t` via `session.refresh` to recover from a
savepoint rollback's attribute-expiry, per the durability doctrine in
`CLAUDE.md`'s "Notification re-escalation backoff" section) — that outer
guard exists for a different failure class (a mid-flush DB error inside the
`begin_nested()` block) and is not replaced by the new inner guard; the two
are complementary layers, not duplicates.

## Test pattern

Each guarded site pins the failure path with a unit test that forces the
`a2a.send` mock to raise and asserts the verb still returns an `ok`
envelope carrying the exception type and recipient in `warning`, e.g.
`test_i_documented_survives_a2a_send_failure` in
`tests/unit/gateway/test_choreographer_doc.py` — RuntimeError raised from
`a2a.send`, asserting `body["status"] == "awaiting_pm_review"` and both
the recipient id and `"RuntimeError"` appear in `body["warning"]`.

## Adding a fifth post-transition notify site

Copy the shape above exactly: broad `except Exception`, log with
`repr(exc)`, return a warning string (never raise), thread it into the
unchanged success envelope, and add a test that forces the send to raise
and asserts the envelope stays `ok`.
