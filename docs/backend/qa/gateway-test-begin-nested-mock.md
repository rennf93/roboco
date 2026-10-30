# Gateway test setup: `begin_nested` async context manager mock

## The gotcha

Several Choreographer code paths wrap a DB write in a savepoint via `async with self.task.session.begin_nested():`. The drift guard's findings-ledger insertion (`pr_gate.py:_scope_relaxation_drift_guard`) is one; the completion-gate findings verify and CEO-approve stamps are others. When a gateway test's `_make_choreographer` (or equivalent fixture) does **not** set `session.begin_nested` as an async context manager mock, the `async with` silently fails on the protocol — `MagicMock` is not an async context manager — and the code inside the savepoint **never executes**. The test still passes because it asserts on the rejection envelope (which is produced before the savepoint), not on the side effect inside it. The findings-ledger write is skipped, and the test gives false confidence.

## The pattern

Every gateway test fixture that constructs a `Choreographer` with a mocked session must include:

```python
base["task"].session.begin_nested = MagicMock(
    return_value=MagicMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=False),
    )
)
```

`__aexit__` must return `False` so the savepoint context does not suppress exceptions raised inside it (matching real SQLAlchemy `begin_nested` behavior). Reference implementations: `test_choreographer_completion_guards.py:43`, `test_choreographer_claim_lock.py:51`, `test_verb_runner.py:89`.

## How to tell if a test is missing it

If a test exercises a path that calls `session.begin_nested()` and the test passes but the side effect inside the savepoint is not asserted, check whether the fixture sets `begin_nested`. A silent `async with` failure produces no error — the code simply doesn't run. The fix is to add the mock, then add an assertion on the side effect (e.g. patch `findings_lib.insert_and_render` as an `AsyncMock` and assert it was called with the expected `origin`, `findings`, and `Finding` field values).

## The test that pins it

`test_scope_relaxation_drift_writes_findings_ledger` in `tests/unit/gateway/test_pr_pass_ci_status_guard.py` patches `findings_lib.insert_and_render`, triggers the drift path, and asserts the mock was called once with `origin=pr_gate` and a `Finding` whose `file` is the drifted path, `severity` is `MAJOR`, and `evidence` contains narrative text. If the `begin_nested` mock is removed, this test fails — the `insert_and_render` mock is never called.