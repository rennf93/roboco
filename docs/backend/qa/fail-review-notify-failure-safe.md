# Site 1a: fail_review's post-transition developer notification is failure-safe

PR #992 (branch `feature/backend/4d62fa12--41b4459e--0664b042`, commit `57a4c44`). Site 1a of the post-transition `a2a.send` guard series — the first of four unguarded notification sites in the choreographer being failure-safed one by one (site 1b `_impl.py request_changes`, site 1c `_impl.py` submit-qa handoff, and the pr_gate `pr_fail` loop-closer follow).

## The failure mode

`fail_review`'s verb body notified the original developer via `A2AService.send` immediately after `runner.run_intent('fail_review')` (previously `qa.py:1124-1131`). `A2AService.send` raises on policy denial (`A2AAccessDeniedError`, `roboco/services/a2a.py:1816`), missing agent lookup (`a2a.py:1792`), conversation/participant validation (`a2a.py:1251/1272/1275`), and transient DB errors mid-flush. Because the notification sat unguarded *after* the transition had already committed, a raise there:

- surfaced to the QA reviewer as an opaque verb error even though the bounce itself succeeded;
- skipped the sandbox teardown that follows the notify;
- could poison the shared session before the root commit — silently rolling back the just-committed `needs_revision` transition, the inserted `task_review_findings` rows, and the structured `qa_notes` note.

The reviewer would see "verb failed", re-issue `fail_review`, and double-bounce a task that had already transitioned.

## What shipped

Two new private helpers in `roboco/services/gateway/choreographer/qa.py`, mirroring the existing sibling template `_pass_review_documenter_handoff` (the QA-pass documenter handoff, which already had exactly this shape):

- **`_fail_review_developer_notify(qa_agent_id, task_id, t, summary)`** — wraps the `a2a.send` in a broad `try/except Exception` that never re-raises. On success it returns `None`; on failure it logs a structlog warning (`task_id` + `error`) and returns a warning string that carries the exception's identity — `type(exc).__name__` plus the message, e.g. `"QA needs-changes transition committed but the developer notification failed (RuntimeError: a2a down). Re-issue the notification via dm."` — so nothing meaningful is swallowed and the reviewer has a concrete remediation path (`dm`). Its docstring carries the sibling's "transition is already committed at this point" comment spelling out why a raise must not escape.
- **`_fail_review_success_env(...)`** — composes fail_review's success envelope, folding the notify-failure warning together with the pre-existing soft above-nudge findings-count hint (`findings_lib.findings_count_hint`) into the single additive `env.warning` channel `pass_review` already uses. Both are optional and never blocking; a clean send with no count nudge yields no warning at all.

The notification *attempt and rendering contract are unchanged*: the body is still `f"QA needs changes.\n{summary}"` with the same ledger rendering, sent to the task's assignee, gated on `t.assigned_to is not None` — only the failure mode degrades. Sandbox teardown (`_teardown_sandbox_best_effort`) now always runs after the notify attempt instead of being skipped on failure. The `fail_review` docstring's contract ("the verb body notifies the original developer via a2a with the same rendering") still holds verbatim.

## Tests

`tests/unit/gateway/test_choreographer_qa.py` gained three cases: the degraded path (send raises → envelope is still `ok` with the warning carrying the exception identity), the clean path (successful send → no warning), and the combined path (send failure + findings-count nudge → both folded into one `env.warning`).

## Scope note

Per the task's instruction, changes are confined to `roboco/services/gateway/choreographer/qa.py` and its tests — this is the leaf for site 1a only. The remaining unguarded sites (1b/1c in `_impl.py`, the pr_gate `pr_fail` loop-closer at `pr_gate.py:514-521`) are separate tasks in the same series; `pr_fail`'s loop-closer already has the guarded shape this fix copies.