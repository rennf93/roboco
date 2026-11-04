# CI Fix: Red Python Quality Gate on PR #1373, Round 4 (Integration Stub)

**Task 17829f2a — PR #1522 (fix PR for cell branch of PR #1373)**

## Root Cause

After the `uv.lock` pip-audit fix (see `ci-pip-audit-fix-stale-lock-pr-1373.md`), the hosted `Python quality gate` was still red. The failing step was `pytest` inside `make quality`, and it only fails when a live Postgres is available. Earlier rounds ran without Postgres, so the DB-backed integration tests were skipped and never exposed it.

Two tests in `tests/integration/test_full_lifecycle_real_db.py` failed:

- `test_cell_and_root_reach_completed_via_gate_and_ceo_approval`
- `test_pr_fail_on_submit_up_then_resubmit_reaches_completed`

Both were refused at `pr_pass` with `CI status lookup failed: _MultiTaskStubGit has no attribute get_pr_ci_status`. The strict CI lookup in `pr_gate.py` (part of this PR's own change) now calls `get_pr_ci_status` on the git service. The `_MultiTaskStubGit` test double did not define it, so the lookup failed closed and the gate refused the pass.

## Solution Applied

One test-only change: `_MultiTaskStubGit` gained

```python
async def get_pr_ci_status(self, slug: str, pr_number: int) -> dict[str, Any]:
    del slug, pr_number
    return {"state": "no_ci_configured"}
```

`roboco/services/git.py` and `roboco/services/gateway/choreographer/pr_gate.py` are unchanged, so the fail-closed behavior is intact. Any future git stub used by a lifecycle test that reaches `pr_pass` needs the same method.

## Verification (dev run against sandbox Postgres and Redis)

- Full `make quality` mirrored the CI setup. Both failing tests pass. Coverage was 91.12% against the Makefile floor of 75%, not 80%.
- Lint, format, mypy, xenon, lint-imports, vulture, bandit, deptry, pip-audit and `reflow_md --check` pass.

## Still failing locally (not caused by this PR)

Stale-base drift outside this PR's files. These may be what keeps the hosted check red and need their own task:

- `test_llm_routing::test_apply_mode_ollama_preserves_compound_row_and_resolution` (`Unknown model kimi-k3.1-code:cloud`)
- `test_provider_routes::test_apply_mode_ollama_without_provider_returns_404`
- `test_provider_routes::test_apply_mode_cost_tiered_is_additive_preserves_global`
- `test_hummin_cli_config::test_main_renders_settings_and_env_file` (extra `ROBOCO_HUMMIN_MCP_TOOLS`)

Environmental only in the agent sandbox: the three `test_stale_review_claim_reaper` tests (no docker) and `test_release_readiness_audit` (no git tags).

## Not verified

The hosted CI result itself; the commit advances the branch head so CI re-runs.

## Pattern

When a change makes a service call strict (fail closed), grep the test doubles that stand in for that service, including integration stubs, and add the new method. Run `make quality` with Postgres and Redis, because DB-backed tests are skipped without them.
