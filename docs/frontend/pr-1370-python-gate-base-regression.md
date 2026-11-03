# PR 1370 red Python quality gate: base regression finding

Task `04d83ee5` (PR #1378, branch `feature/frontend/4559be66--241582c0--04d83ee5`).

## Outcome

The red "Python quality gate" on PR #1370 is not caused by the frontend feature. `make quality` run in the task worktree after `sync_branch` (which reported the branch already current) fails at the **pytest** step: 9 failed, 17031 passed. None of the failing tests touch `panel/src` or `docs/frontend`; they come from base/slave-branch drift and were deliberately left unedited.

## Failing tests (base regression, for the PM to escalate)

| File | Tests |
|---|---|
| `tests/unit/llm/providers/test_hummin_cli_config.py` | `test_main_renders_settings_and_env_file` |
| `tests/unit/runtime/test_stale_review_claim_reaper.py` | `test_dead_claimant_released`, `test_dispatch_pause_defers_release`, `test_sweep_wires_the_review_pass` |
| `tests/unit/test_ci_workflow_paths.py` | `test_every_markdown_file_the_prose_gate_checks_fires_ci`, `test_every_python_file_fires_ci`, `test_every_workflow_path_filter_matches_a_tracked_file` |
| `tests/unit/test_required_checks.py` | `test_every_file_codeql_analyzes_is_under_its_workflows_paths[code-ql.yml / codeql-js-ts.yml]` |

## What this task changed

Only prettier formatting, which had turned the `panel-gate` prettier step red:

- `panel/src/components/dashboard/playbook-approved-list.tsx`: the empty-state paragraph is wrapped across lines.
- `panel/src/components/dashboard/__tests__/playbook-approved-list.test.tsx`: the `renderQueue` default-argument `QueryClient` is reformatted.

No behavior changed. No Python or config files were edited.

## Verification notes

- `make panel-gate`: prettier now passes. Vitest passes except one `ai-routing-card` picker test that timed out under load; it passes in isolation (60/60).
- `make quality` takes roughly 10 minutes.

## Follow-up

Until the nine Python tests above are fixed on the base, the Python quality gate will stay red on this branch and `pr_pass` will keep refusing. The PM should raise this as a base regression.
