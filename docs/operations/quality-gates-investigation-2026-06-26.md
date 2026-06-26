# Non-coverage `make quality` gate investigation — 2026-06-26

**Scope:** Every gate in `make quality` except the pytest/coverage step. **Master HEAD at time of investigation:** `e2f7097` (Persist the PM-respawn counter across orchestrator restarts). **Workspace:** `/data/workspaces/roboco-api/backend/be-dev-1`. **Outcome:** All non-coverage gates already exit `0`. **No code change required.**

## Gate-by-gate result

| # | Gate | Command (from `Makefile`) | Exit | Notes |
|---|------|---------------------------|------|-------|
| 1 | ruff format | `uv run ruff format --check .` | 0 | 854 files already formatted |
| 2 | ruff check | `uv run ruff check .` | 0 | "All checks passed!" |
| 3 | reflow_md | `uv run python scripts/reflow_md.py --check` | 0 | "OK: no hard-wrapped markdown prose in scope." |
| 4 | mypy | `uv run mypy roboco/ tests/` | 0 | "Success: no issues found in 849 source files" |
| 5 | xenon | `uv run xenon --max-absolute B --max-modules A --max-average A roboco/` | 0 | no output, exit 0 |
| 6 | radon mi | `uv run radon mi roboco/ -nc -s` | 0 | 6 files listed at C-grade (see note); `-s` only displays MI values, does not fail |
| 7 | radon cc | `uv run radon cc roboco -nc` | 0 | 4617 blocks, average A (2.92) |
| 8 | vulture | `uv run vulture roboco/ tests/ vulture_whitelist.py --min-confidence 100` | 0 | no dead-code hits |
| 9 | bandit | `uv run bandit -r roboco/ -ll` | 0 | 0 issues identified (1 nosec waiver, 55 low-severity display) |
| 10 | pip-audit | `uv run pip-audit --ignore-vuln CVE-2025-3000` | 0 | no known vulnerabilities; documented torch JIT waiver applies |
| 11 | deptry | `uv run deptry roboco/` | 0 | "Success! No dependency issues found." |
| 12 | alembic --sql | `uv run alembic upgrade head --sql > /dev/null` | 0 | migrations parse successfully |
| 13 | import-linter | `uv run lint-imports` | 0 | 2 contracts kept, 0 broken |
| 14 | foundation-check | `make foundation-check` | 0 | lifecycle artifacts stable; postgres enum-parity skip-on-no-DB |

## Note on `radon mi`

The `-s` flag in `radon mi` is "show the actual MI value in results" (display), **not** "fail on low MI". With the default `-n A` (minimum A) and `-x C` (maximum C) display range, files at C-grade are listed for visibility but the gate does not fail. The 6 files at C-grade on master HEAD are:

- `roboco/api/routes/tasks.py` (MI 7.65)
- `roboco/runtime/orchestrator.py` (MI 0.00)
- `roboco/services/git.py` (MI 0.00)
- `roboco/services/optimal.py` (MI 0.00)
- `roboco/services/task.py` (MI 0.00)
- `roboco/services/gateway/choreographer/_impl.py` (MI 0.00)

An MI of `0.00` is unusual; it indicates these files have either very high cyclomatic complexity, very low comment ratio, or both, contributing to the maintainability formula's saturation at the floor. They are pre-existing on master and are not a regression of this task; future refactors may raise them, but the gate as written does not require it.

## Out of scope (intentionally not investigated here)

- The pytest/coverage step in `make quality` (`uv run pytest -q --cov=roboco --cov-report=term-missing --cov-fail-under=80`) is the **coverage gate** and is owned by task `813521ad` ("Lift unit coverage on autonomous-maintenance modules to restore the 80% gate"). In this sandbox the step additionally fails for environment-specific reasons (missing `ROBOCO_ENCRYPTION_KEY` env, missing git tags) which CI resolves.
- `panel-gate` / `panel-quality` (Next.js) is a separate gate owned by the frontend cell.