# CI Diagnosis: PR #718's Red Python Quality-Gate Check (Unresolved, Inherited)

**PRs #718 / #746 — Diagnosed, not resolved by this task**

## Root Cause

PR #718's Python quality-gate CI check stayed red across two review rounds even though the PR's own diff is a single 25-line markdown file (`docs/positioning/factory-ai-rebrand-2026-06.md`) with no Python, config, or test changes. Two mechanical causes were ruled out or confirmed by this task:

1. **Branch staleness** — ruled out. `sync_branch` against the branch's root base (`feature/backend/984d8b7c--a9a030d8`) returned `superseded`/no commits to rebase in every round; the branch was already current.
2. **The real cause**: `make quality`'s "markdown prose (no hard-wrapping)" step (`scripts/reflow_md.py --check`) — a check distinct from ruff/mypy/xenon/bandit/coverage that both prior PR-gate rounds overlooked. `docs/positioning/factory-ai-rebrand-2026-06.md` has hard-wrapped prose that fails this check. The hard-wrap predates this task: it was introduced by completed sibling tasks `4db76cc9` (added the file) and `976a22aa` (added a source line, still hard-wrapped).

A separate, unrelated red step was also observed: `pytest`/coverage sits at 73.47% against an 80% threshold — a pre-existing, codebase-wide gap, not caused by this file or this task.

## Why It Was Not Fixed Here

This task's own instructions explicitly forbade editing `docs/positioning/factory-ai-rebrand-2026-06.md` (it had already reviewed clean on content in both rounds). Running `make reflow-docs` on the file would satisfy the reflow-check step but requires editing the protected file, which is out of this docs-only task's authorized scope. An earlier attempt in this task's history did run the reflow and was reverted per QA finding `F-ac93df6a` for exactly this reason — the revert restored the file byte-for-byte (1265 bytes) to its pre-edit content.

## Recommended Follow-Up

A separate, properly-scoped task should run `make reflow-docs` (or `scripts/reflow_md.py`) against `docs/positioning/factory-ai-rebrand-2026-06.md` and commit the result — a mechanical, content-preserving rewrap (one-line-per-paragraph), verified token-invariant by the script's own safety check. That follow-up is also the right place to address the separately-tracked 73.47%-vs-80% coverage gap if it still blocks the gate at that point.

## Pattern

`make quality` enforces markdown prose formatting (`scripts/reflow_md.py --check`) as its own step, independent of ruff/mypy/xenon/bandit/coverage. A PR whose diff is markdown-only and shows a red Python quality gate should check this step specifically, not just the Python linters — and any file added by an earlier task with hard-wrapped prose will keep failing every later PR that shares its branch until a task authorized to edit that file runs the reflow.
