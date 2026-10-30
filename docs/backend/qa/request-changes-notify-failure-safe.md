# Site 1b: request_changes' post-transition notification to the revision owner is failure-safe

PR #993 (branch `feature/backend/4d62fa12--41b4459e--0bbdd274`, commit `98d07b3f`). Site 1b of the post-transition `a2a.send` guard series — the second of four unguarded notification sites in the choreographer (site 1a `qa.py fail_review`, documented in `fail-review-notify-failure-safe.md`; sites 1c `_notify_qa` and 1d `doc.py _handoff_to_cell_pm` follow). The site's own comment claimed it "mirrors fail_review / the pr_fail loop-closer" — yet unlike those two precedents it was never wrapped. This fix makes that comment true.

## The failure mode

`request_changes` (the PM merge reject, `awaiting_pm_review` → `needs_revision`) sent its reject-rendering a2a message to the revision owner via an inline, unguarded `await self.a2a.send(...)` immediately after the transition ran (`_impl.py:8751-8758` pre-fix). `A2AService.send` is not best-effort: it raises on policy denial (`A2AAccessDeniedError`, `roboco/services/a2a.py:1816`), missing agent lookup (`a2a.py:1792`), conversation/participant validation (`a2a.py:1251/1272/1275`), and transient DB errors mid-flush. A raise there, after the `needs_revision` transition was composed and committed:

- surfaced to the PM as an opaque verb error even though the bounce itself succeeded;
- could poison the shared session before the root commit — silently rolling back the just-committed transition, the inserted `task_review_findings` rows, and the structured `pm_notes` note — silently un-doing a bounce the PM believes they delivered.

The revision owner would see "verb failed" with no remediation matching reality, and the reject reason would be stranded.

## What shipped

The inline send was extracted into a same-file helper in `roboco/services/gateway/choreographer/_impl.py`, mirroring the in-repo template `_pass_review_documenter_handoff` (`qa.py:942-974`) and its second precedent, pr_gate's `pr_fail` loop-closer (`pr_gate.py:514-521`):

- **`_notify_request_changes_owner(pm_agent_id, task_id, t, summary)`** — preserves the original skip condition (`assigned_to is None` or equals the PM: no distinct owner to notify), then wraps the `a2a.send` in a broad `try/except Exception` that never re-raises. On success it returns `None`; on failure it logs a structlog warning carrying `task_id`, `recipient=str(t.assigned_to)`, and `error=repr(exc)`, and returns a warning string naming the recipient and the exception repr — e.g. `"request_changes transition committed but the a2a notification to the revision owner (<uuid>) failed (...). The reject reason is on the task's findings ledger — re-issue it via dm."` — so nothing meaningful is swallowed and the PM has a concrete remediation path (`dm`). Its docstring carries the template's "the needs_revision transition plus the ledger rows and the pm_notes note are already committed at this point" comment spelling out why a raise must not escape.
- **`request_changes` body** — calls the helper after `runner.run_intent('request_changes')` and folds the returned warning into `env.warning` alongside the pre-existing findings-count hint (`findings_lib.findings_count_hint`), same additive `env.warning` channel `fail_review` and `pass_review` use. A clean send with no count nudge yields no warning at all.

The notification *attempt and rendering contract are unchanged*: body is still `f"PM merge review needs changes.\n{summary}"`, sent to the task's assignee — only the failure mode degrades. The ok envelope (`status=needs_revision`) and its `next_hint` are unchanged, and the `request_changes` docstring contract ("a2a-delivers the same rendering to the new owner so the reject reason is never stranded") still holds verbatim. Per the dev's decision note, the helper is a byte-identical port of the equivalent guarded helper already on trunk, minimizing merge risk.

## Tests

Verified by the developer with `make gate` plus the full `tests/unit/gateway` suite (1662 passed, 5 skipped, 0 failures); ruff/mypy/xenon clean. The behavioral guard tests for this helper land with the series' combined unit-tests leaf, which is dependency-gated on all four guard sites.

## Scope note

Per the task's instruction, changes are confined to `roboco/services/gateway/choreographer/_impl.py` — this is the leaf for site 1b only, sequenced before the sibling `_notify_qa` leaf (same file). The remaining unguarded sites (1c `_notify_qa` at `_impl.py:3941-3958`, 1d `doc.py _handoff_to_cell_pm` at `doc.py:658-676`) are separate tasks in the same series.