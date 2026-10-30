# Units 2a/2b/3: fail_review + request_changes raise-broadly tests, and the final unguarded-send sweep

PR #1050 (branch `feature/backend/4d62fa12--41b4459e--6b1d253a`, commit `b0ed2533`). This is the combined unit-tests leaf for the post-transition `a2a.send` guard series, dependency-gated on all four guard sites: 1a `qa.py fail_review` (`fail-review-notify-failure-safe.md`), 1b `_impl.py request_changes` (`request-changes-notify-failure-safe.md`), 1c `_impl.py _notify_qa`, and 1d `doc.py _handoff_to_cell_pm`. No production code changed here — all four guard sites were already landed by sibling tasks before this one claimed; the remaining work was the missing test coverage plus a recorded final sweep.

## What shipped

Two parametrized unit tests, one per bounce verb, each run over both a generic `RuntimeError` and the actual policy-denial exception `A2AAccessDeniedError` (`roboco/services/a2a.py:1816`) — proving the guards catch broadly, not just the narrower transient-error shape the site-1a/1b fix-up tests already covered:

- **`test_fail_review_bounce_survives_a2a_raise_broadly`** (`tests/unit/gateway/test_choreographer_qa.py`) drives `fail_review` with `a2a.send` stubbed to raise. Asserts: an ok envelope (`error` is `None`), `status == "needs_revision"`, a non-`None` `warning` string, `task_svc.session.add.call_count == len(_FAIL_REVIEW_ISSUES)` (both findings' `task_review_findings` ledger rows persisted before the a2a step ran), `task_svc.qa_fail.assert_awaited_once()` (the transition committed regardless of the notify outcome), and the id-prefixed `[F-<id8>]` rendering (`render_finding_line`) surviving into the structured `qa_notes` mirror.
- **`test_request_changes_survives_a2a_raise_broadly`** (`tests/unit/gateway/test_choreographer_request_changes.py`) is the PM-side equivalent: same ok/`needs_revision` envelope and `warning` assertions, the `task_review_findings` row persisting, and the `[F-<id8>]` rendering surviving into the structured `pm_notes` mirror.

Both tests make the mocked `session.add` assign a real `uuid4()` to the inserted `TaskReviewFindingTable` row via a shared `_add_assigns_id` side effect. Without it, `row.id` stays `None` on a mocked session — `default=uuid4` is a SQLAlchemy Python-side default only applied at a real flush against an engine — which would make the `[F-<id8>]`-prefixed rendering unobservable without a live database, and the acceptance criteria explicitly require asserting that rendering survives.

## Recorded sweep: zero unguarded `await self.a2a.send` sites

Grep alone cannot prove a call site is guarded — a hit's surrounding scope has to be read. Every `await self.a2a.send` under `roboco/services/gateway/choreographer/` was enumerated and its containing `try`/`except` inspected directly:

| File:line | Helper | Guard |
|---|---|---|
| `qa.py:976` | `_pass_review_documenter_handoff` (QA-pass → documenter handoff) | `try:` / `except Exception as exc:` |
| `qa.py:1064` | `_fail_review_developer_notify` (fail_review bounce → developer notify) | `try:` / `except Exception as exc:` |
| `pr_gate.py:520` | `pr_fail` loop-closer (→ owning PM notify) | `try:` / `except Exception:` |
| `doc.py:673` | `_handoff_to_cell_pm` | `try:` / `except Exception as exc:` |
| `_impl.py:3995` | `_notify_qa` (submit-qa handoff) | `try:` / `except Exception as exc:` |
| `_impl.py:9050` | `_notify_request_changes_owner` (request_changes → revision-owner notify) | `try:` / `except Exception as exc:` |

All six sites sit inside a broad exception guard. Zero unguarded `await self.a2a.send` call sites remain in the package — the series is closed.

## Verification

`make lint` clean on the two touched test files. `.venv/bin/python -m pytest tests/unit/gateway/` — 1675 passed, 5 skipped (DB-only), 0 failures. `make test`/`quality`/`gate`'s sync/docker prerequisites were broken in the dev's sandboxed workspace (pre-existing, unrelated to this change), so the direct pytest run above is the verification of record for the two new tests.
