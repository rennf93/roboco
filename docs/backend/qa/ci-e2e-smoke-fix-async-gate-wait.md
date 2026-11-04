# e2e-smoke fix: wait for the async quality gate before asserting `awaiting_qa`

Task `7e53b0ef` (PR #1497, follow-up to PR #1434 round-5 finding `b74581c7`).

## Problem

The `e2e-smoke` CI check failed on the PR #1434 head commit. That PR moved the developer quality gate off the `i_am_done` request path:

- `i_am_done` now parks the task in `verifying` and launches the gate as a background job (`_defer_quality_gate` in `dev.py`: stamp the gate run, commit the session, then launch).
- The gate's verdict callback is what submits the task to `awaiting_qa` (gate pass) or routes it back via the `gate_failed` transition (gate fail).

Two smoke flows still assumed the old synchronous behavior and asserted `status == "awaiting_qa"` immediately after `i_am_done`:

- `dev_arc` in `tests/e2e_smoke/arcs.py` (shared by most lifecycle tests)
- `test_delegation_lane_devops_claims_and_delivers` in `tests/e2e_smoke/test_devops_gate.py`

## Fix

Test-only; no production code changed. Both call sites now poll with the existing `wait_for_status` helper:

```python
expect_ok(dev.flow("i_am_done", task_id=tid), "dev i_am_done")
wait_for_status(stack, task_id, "awaiting_qa", timeout=GATE_WAIT_SECONDS)
```

`GATE_WAIT_SECONDS = 60.0` (defined in `tests/e2e_smoke/arcs.py`, imported by `test_devops_gate.py`) is the upper bound for the gate verdict to land.

## Guarantee preserved

A task still cannot reach `awaiting_qa` before the background gate passes. The smoke flows now observe that ordering instead of assuming it: they only reach `awaiting_qa` once the callback has submitted the task.

## Writing new smoke flows

Any flow that calls `i_am_done` must not assert the post-submit status synchronously. Call `wait_for_status(..., "awaiting_qa", timeout=GATE_WAIT_SECONDS)` instead. The immediate status right after `i_am_done` is `verifying`.

## Verification

Developer ran the full `make e2e-smoke` suite against a sandbox database: 75 passed, 7 skipped; `make gate` green.
