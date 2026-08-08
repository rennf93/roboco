# CodeQL Workflows

RoboCo uses two separate GitHub Actions workflows to run CodeQL analysis. They were split so that backend-only pull requests do not block on the JavaScript/TypeScript analyzer, and frontend-only pull requests do not wait for the Python analyzer.

## Why the workflows are split

The original `.github/workflows/code-ql.yml` ran both the `python` and `javascript-typescript` analyzers under a single path filter. Any change to `roboco/**`, `agents/**`, `alembic/**`, `scripts/**`, `panel/**`, `pyproject.toml`, or the workflow file itself triggered both analyzers. This caused backend-only PRs to fail when the shared JavaScript/TypeScript analyzer tripped over frontend state that was unrelated to the diff.

The fix separates the analyzers by language and by the code they actually scan.

## Workflow files

| File | Name | Language | Triggers |
|------|------|----------|----------|
| `.github/workflows/code-ql.yml` | CodeQL Python | `python` | Changes to `roboco/**`, `agents/**`, `alembic/**`, `scripts/**`, `pyproject.toml`, or `.github/workflows/code-ql.yml` |
| `.github/workflows/codeql-js-ts.yml` | CodeQL JavaScript/TypeScript | `javascript-typescript` | Changes to `panel/**` or `.github/workflows/codeql-js-ts.yml` |

Both workflows also run:

- On a weekly schedule (`0 0 * * 1`).
- On `workflow_dispatch`.

## Job names

The visible job names are intentionally unchanged:

- `Analyze (python)`
- `Analyze (javascript-typescript)`

This preserves existing branch-protection rules and PR check expectations. Only the workflow display names changed:

- `CodeQL` → `CodeQL Python`
- New workflow: `CodeQL JavaScript/TypeScript`

## Concurrency

Each workflow uses a shared concurrency group keyed by `github.workflow` plus either `github.head_ref` (for `pull_request`) or `github.ref_name` (for `push`). This collapses duplicate `push` and `pull_request` runs for the same branch and cancels the older one.

`github.ref` is intentionally not used here because it differs between event types (`refs/pull/<n>/merge` vs `refs/heads/<branch>`), so it would never deduplicate the two event shapes.

## Branch protection

If any branch-protection rule matched the old workflow by its display name (`CodeQL`), update the rule to match by the individual job names (`Analyze (python)` and `Analyze (javascript-typescript)`) rather than the workflow name. The job names did not change.

## When both workflows run

A PR that touches both backend and frontend files (for example, `roboco/**` and `panel/**`) will trigger both workflows, as intended.

## Scope-gating against a task's declared file scope

CodeQL analyzes an entire language surface each time it runs (everything under the `paths:` filter above), not just the diff — so a check-run can fail on a pre-existing alert in a file a task never touched. The in-path PR-review gate's CI-status guard (`pr_pass` -> `_ci_status_guard` in `roboco/services/gateway/choreographer/pr_gate.py`) accounts for this: when a failing `Analyze (python)` or `Analyze (javascript-typescript)` check has zero overlap between its analyzed-language paths (the same list above) and the reviewed task's declared `intends_to_touch` scope, that check no longer blocks `pr_pass` on its own. A task with no declared scope, or a genuinely in-scope CodeQL failure, still blocks exactly as before; any other failing check (tests, lint, an in-scope CodeQL alert) still blocks regardless.

The check-name-to-scope mapping (`_CODEQL_CHECK_SCOPES`) matches by exact GitHub check-run name. A renamed CodeQL workflow, or a new language/matrix entry added without a matching map entry, has no scope to compare against — it falls back to the conservative default and still blocks `pr_pass`, the same as before this change. Keep `_CODEQL_CHECK_SCOPES` in sync with any future `code-ql.yml` / `codeql-js-ts.yml` job-name or `paths:` change, or a legitimately out-of-scope failure will start blocking again silently.
