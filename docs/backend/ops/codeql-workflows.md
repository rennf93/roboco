# CodeQL Workflows

RoboCo uses two separate GitHub Actions workflows to run CodeQL analysis. They were split so that backend-only pull requests do not block on the JavaScript/TypeScript analyzer, and frontend-only pull requests do not wait for the Python analyzer.

## Why the workflows are split

The original `.github/workflows/code-ql.yml` ran both the `python` and `javascript-typescript` analyzers under a single path filter. Any change to `roboco/**`, `agents/**`, `alembic/**`, `scripts/**`, `panel/**`, `pyproject.toml`, or the workflow file itself triggered both analyzers. This caused backend-only PRs to fail when the shared JavaScript/TypeScript analyzer tripped over frontend state that was unrelated to the diff.

The fix separates the analyzers by language and by the code they actually scan.

## Workflow files

| File | Name | Job (check) | Language | Paths |
|------|------|-------------|----------|-------|
| `.github/workflows/code-ql.yml` | CodeQL Python | `code-ql` | `python` | `roboco/**`, `agents/**`, `alembic/**`, `scripts/**`, `tests/**`, `.github/scripts/**`, `docker/decisions/**`, `vulture_whitelist.py`, `pyproject.toml`, `.github/workflows/code-ql.yml` |
| `.github/workflows/codeql-js-ts.yml` | CodeQL JavaScript/TypeScript | `codeql-js-ts` | `javascript-typescript` | `panel/**`, `motion/**`, `video-renderer/**`, `docs-redirects/**`, `.github/workflows/codeql-js-ts.yml` |

CodeQL analyzes every file of its language in the checkout, whatever the paths say, so each workflow's paths list every directory that holds such a file: a change outside them would land unanalyzed (#1196's two `motion/` alerts reached slave that way, when the JavaScript/TypeScript paths were `panel/**` alone). `tests/unit/test_required_checks.py` fails when a tracked file of either language falls outside its workflow's paths.

A push runs a workflow only when it touches the workflow's paths. A pull request into `master` or `slave` always starts both workflows, and the job's first step (`.github/scripts/required_checks.py match-paths`) applies the same paths: the analysis steps run only when the PR touches them, and the job passes without analyzing otherwise. A PR into `master` always analyzes.

Both workflows also run:

- On a weekly schedule (`0 0 * * 1`).
- On `workflow_dispatch`.

## Job names

The jobs are named `code-ql` and `codeql-js-ts`, two of the six checks master's ruleset (`master-rule`) requires. A pull request must start the workflow whatever it touches because a workflow that a `paths:` filter skips never reports its check, and a required check that never reports blocks the PR. `tests/unit/test_required_checks.py` pins each required name to its job.

Until 2026-10-30 the jobs were `Analyze (python)` and `Analyze (javascript-typescript)`. A branch cut before the rename still reports those names from its push runs, so the PR gate's scope map keeps them (see below).

## Concurrency

Each workflow uses one concurrency group for its `push` and `pull_request` runs of a branch, `check-<job name>-` plus either `github.head_ref` (for `pull_request`) or `github.ref_name` (for `push`). This collapses duplicate `push` and `pull_request` runs for the same branch and cancels the older one.

`github.ref` is intentionally not used here because it differs between event types (`refs/pull/<n>/merge` vs `refs/heads/<branch>`), so it would never deduplicate the two event shapes. The group carries the check's name rather than `github.workflow` so a run of the pre-rename definition (on a branch cut before it) never shares a group with a current one: if it did, a cancelled run would leave a check under a name no later run reports again.

## Branch protection

`master-rule` requires checks named `code-ql` and `codeql-js-ts` (with `ci`, `panel-ci`, `e2e-smoke` and `agent-image-smoke`). A rule must match the job name exactly; the workflow display names (`CodeQL Python`, `CodeQL JavaScript/TypeScript`) are not check names.

## When both workflows run

A PR that touches both backend and frontend files (for example, `roboco/**` and `panel/**`) analyzes with both workflows, as intended.

## Scope-gating against a task's declared file scope

CodeQL analyzes an entire language surface each time it runs (every file of its language, which the `paths:` above cover), not just the diff — so a check-run can fail on a pre-existing alert in a file a task never touched. The in-path PR-review gate's CI-status guard (`pr_pass` -> `_ci_status_guard` in `roboco/services/gateway/choreographer/pr_gate_pass.py`) accounts for this: when a failing `code-ql` or `codeql-js-ts` check (or its pre-rename name, `Analyze (python)` or `Analyze (javascript-typescript)`) has zero overlap between its analyzed-language paths (the same list above) and the reviewed task's declared `intends_to_touch` scope, that check no longer blocks `pr_pass` on its own. A task with no declared scope, or a genuinely in-scope CodeQL failure, still blocks exactly as before; any other failing check (tests, lint, an in-scope CodeQL alert) still blocks regardless.

The check-name-to-scope mapping (`_CODEQL_CHECK_SCOPES`) matches by exact GitHub check-run name. A renamed CodeQL workflow, or a new language/matrix entry added without a matching map entry, has no scope to compare against — it falls back to the conservative default and still blocks `pr_pass`, the same as before this change. `tests/unit/test_required_checks.py` fails when `_CODEQL_CHECK_SCOPES` and the workflows' job names or `paths:` drift apart.
