# Coroner stranded-block context: block_reason, escalation_history, and where they surface

When `StrategyEngine` notices a task stuck in `blocked` past `strategy_stranded_blocked_minutes`, it opens a Coroner autopsy (`CoronerEngine.open_for_incident(..., kind="stranded", extra_context=...)`) so the Auditor can investigate why. The `extra_context` payload — `block_reason`, `time_blocked`, `escalation_history` — used to carry misleading or dead data. This fixes that (PR #1064, closing the 4 pr_gate findings on PR #1058) and documents where each field now comes from and where it's actually read.

## `_stranded_context` (roboco/services/strategy_engine.py)

`StrategyEngine._stranded_context(incident)` is the single place all three fields are derived, extracted out of `_trigger_coroner_incident` to keep that method's xenon complexity down after the fix added a second audit-query branch:

- **`block_reason`** — `_derive_block_reason(task)` parses the *last* `'[BLOCKED - <TYPE>]\nReason: ...\nWhat's needed: ...'` note appended to `task.dev_notes` (the format `TaskService` appends at `task.py:6195-6201` when a task is blocked with a reason). It falls back to `task.blocker_resolver_type.value` (`'human'`/`'agent'`) only when no such note exists — e.g. a task blocked via a bare dependency `block()` call that never appended reason text. Previously `block_reason` was *always* `blocker_resolver_type.value`, which answers "who resolves this" rather than "why is it stuck".
- **`escalation_history`** — the same audit-log query that used to count only `task.blocked` rows now also counts `task.escalated` / `task.escalated_to_main_pm` rows for the incident in one `SELECT` (three `func.count(case(...))` columns), and renders as `"escalated {n} time(s) (blocked {m} time(s), revision_count={r})"`. Previously it only reported the blocked-count and `revision_count` — there was no escalation data in it at all despite the field's name.
- **`time_blocked`** — unchanged in shape (minutes elapsed since the latest `task.blocked` audit row, falling back to `updated_at`), but now derived in the same query as the other two instead of a separate one.

## Where the context actually reaches the Auditor

Before this fix, `block_reason`/`time_blocked`/`escalation_history` were written into the `coroner_incident` marker on the exploration task (`CoronerEngine._originate`) but nothing ever read them back out — write-only. Two consumers now render them:

1. **The exploration-task description** (`CoronerEngine._originate`, `roboco/services/coroner_engine.py`): for `kind == "stranded"` with non-empty `extra_context`, a `"Stranded-block context: block reason — ...; time blocked — ...; escalation history — ..."` paragraph is appended to the task description the Auditor is assigned.
2. **The rendered Coroner prompt** (`AgentOrchestrator._build_coroner_prompt`, `roboco/runtime/orchestrator.py:18141`): for `kind == "stranded"`, a `## Stranded-block context` section (Block reason / Time blocked / Escalation history, read back off the `coroner_incident` marker) is inserted into the one-shot spawn prompt ahead of the evidence-gathering instructions.

Either surface alone would close the write-only gap; both are populated so the Auditor sees the same facts whether it re-reads the task description or the prompt it was spawned with.

## Test coverage

`tests/unit/services/test_strategy_engine.py::test_stranded_time_blocked_uses_audit_event_not_updated_at` seeds a blocked task with a real `'[BLOCKED - <TYPE>]'` dev_notes note plus a `task.escalated` audit row, then asserts all three marker fields: `block_reason` contains the seeded reason/what's-needed text (and does NOT contain `"human"`, guarding against a regression back to `blocker_resolver_type`), `escalation_history` contains `"escalated 1 time"`, and `time_blocked` is still derived from the audit event rather than `updated_at`. Previously only `time_blocked` was asserted.
