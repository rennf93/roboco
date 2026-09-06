# CI Fix: Round-3 Revert of a Wrong security.py Mypy Fix

**PR #999 (task 590d5d1b) — Resolved**

## Root Cause
PR #934's gate revision round 3 bounced on finding `a8b31711` (blocker): red quality-gate CI in `roboco/security.py` + `tests/unit/test_security.py`, attributed to guard-core 3.12.0 mypy incompatibilities introduced by commit `74a06513` sitting in the branch base. The finding's recorded fix recipe — replicate PR #946 / squash `1f709714` (wrap the `trusted_proxies` value in `cast()` to `list`, add `type: ignore` comments) — was itself already applied at the branch tip (commit `e8f114b46`) and was **the thing causing the red CI**, not the fix for it: `cast()`-to-`list` plus the unnecessary `type: ignore`s produced 9 mypy errors (unused-ignore + list-vs-tuple arg-type on `SecurityConfig`), reproduced locally under both guard-core 3.12.0 and 3.16.0. `origin/slave`'s HEAD already carried the canonical, green content — native tuple forms, zero casts, zero `type: ignore`s — and that content type-checks clean under both guard-core versions. CI at the pre-fix cell head (`062c8ef7`, 2026-08-24) was fully green, confirming the guard-core-3.12.0-broke-it premise was dead on arrival.

## Solution Applied
Restored both files byte-identical to `origin/slave`'s tip (`git show origin/slave:<path>`), reverting the wrong `cast()`/`type: ignore` fix entirely rather than patching around it:
- `roboco/security.py`: `trusted_proxies` back to a native tuple, all casts and `type: ignore` comments removed.
- `tests/unit/test_security.py`: all three `type: ignore` comments removed; the `block_cloud_providers` assertion re-derived from `VALID_CLOUD_PROVIDERS` (matching slave); docstring updated.

The digest-related files touched by this branch's other lineage (`roboco/utils/shipped_work_digest.py`, `roboco/runtime/orchestrator.py`, `roboco/services/megaphone_engine.py` and their tests) were explicitly left untouched — this leaf's scope was the two security files only.

## Impact
- **Scope:** `roboco/security.py` + `tests/unit/test_security.py` only.
- **Risk:** Low — this is a revert to already-proven-green content, not new logic.
- **Behavior:** No change to security middleware behavior versus `origin/slave`; the branch now matches slave's canonical form exactly.
- **Verification:** `make gate` green (mypy clean across 1521 files including both restored files; ruff format/check clean; xenon clean; import-linter clean) plus the full security/drift-guard suite (96 tests) passing. QA independently cross-checked the restored bytes against its own slave-tracked copy of `security.py` and confirmed byte-identity.

## Pattern
**A gate finding's own recorded fix recipe can be the regression, not the remedy** — verify a finding's blamed commit actually predates the red CI before replicating its prescribed fix. Here, the finding's own root-cause narrative (guard-core 3.12.0 broke it) didn't hold up against a simple check: did CI pass at the commit right before the "fix" landed? It did. When a security-relevant file has multiple overlapping tasks in flight on different branches (this task, `92a48cc0`, and `1073f708` all touched `roboco/security.py`'s guard-core mypy story within the same week per the 2026-08-31 CEO flag), the durable canonical version is `origin/slave`'s tip — extract it directly (`git show origin/<branch>:<path>`) rather than hand-reconstructing a diff, and diff-check the restored bytes before committing.
