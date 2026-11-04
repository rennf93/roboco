# CI Fix: Red Python Quality Gate on PR #1373 (Stale Lock, pip-audit)

**Task 928b741c — PR #1419 (fix PR for cell branch of PR #1373)**

## Root Cause

The red `Python quality gate` on PR #1373 was not caused by the functional commits on that branch. The `git.py` `GitService._resolve_ci_head_sha` fail-closed behavior, the `pr_gate.py` `_resolve_ci_status` strict lookup, and their tests were untouched by this fix. The cause was the stale base: the branch's `uv.lock` pinned ten dependencies that have published security advisories, so the `pip-audit` step failed (48 advisories in total). The affected packages were aiohttp, certifi, dnspython, filelock, mcp, msgpack, multidict, opentelemetry-api, pyjwt and urllib3.

Running the full `make quality` (not only `make gate`) on the branch was what located the failing step. Reflow, mypy, ruff, radon, vulture, bandit, deptry, alembic `--sql`, lint-imports and foundation-check were already clean.

## Solution Applied

`uv.lock` was regenerated with the project's `make lock` target, which resolves the packages above to patched versions (for example aiohttp 3.14.4 to 3.14.5). Two points matter for anyone re-running it:

- The lock keeps `revision = 5` and the `rich<15.0.0` cap from `pyproject.toml`. An earlier attempt used a different uv in a throwaway Makefile and dropped the `rich` cap, so always use `make lock`, never a bare `uv lock`.
- An earlier revision of this fix also rewrote `tests/unit/billing/test_pricing.py` to freeze the date for "Sonnet-5 promo" tests. That was a misreading (`pricing.py` has no promo window), and the file was restored to the base version. The only file changed by the final fix is `uv.lock`.

After the fix `make pip-audit` reports no known vulnerabilities.

## Impact

- **Scope:** dependency lock only; no application code or test changes.
- **Risk:** filelock and multidict took major-version bumps (filelock 3 to 4, multidict 6 to 7). A full pytest run in the dev sandbox showed no new failures from the bumps.
- **Not verified locally:** the 80% coverage floor. DB-backed tests skip without Postgres in the agent sandbox, so CI is the authority for that step.

## Pattern

When `Python quality gate` is red but the branch's own diff is unrelated to the failing step, run the full `make quality` and compare against the base before touching code. `pip-audit` failures on an old base are fixed by regenerating the lock with `make lock`, which pulls patched versions while preserving the repo's pinned constraints and lock revision. Do not hand-edit `uv.lock`.
