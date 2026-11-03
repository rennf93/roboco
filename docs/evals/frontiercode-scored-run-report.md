# Golden-task harness: scored-run report (2026-11-03)

**Scored runs executed: 0.**
**Scores reported: none.**
**Parity with FrontierCode 1.1: not claimed.**

This report records what was attempted. It contains no score, because no harness run completed. Every figure below is a count read from the checked-in code; none comes from a run, so none has a run id.

## What the harness is

- Entry point: `python -m roboco.eval run --role <slug> --cohort <name> [--fixtures a,b] [--json-out path]` (`roboco/eval/__main__.py`).
- Runner: `EvalRunner.run_cohort` (`roboco/eval/runner.py`). It replays fixtures through the real delivery lifecycle in a disposable stack: a fake GitHub REST, a local git origin, a scratch database.
- Real spawn: `OrchestratorStageSpawner` drives one `AgentOrchestrator` container spawn per turn (`runner.py`).
- Scoring: deterministic metrics plus a local-model judge. The judge output is marked `"non_deterministic": true`.
- Scope: twelve fixtures exist. The default `--fixtures` set is the six developer-role ones (`roboco/eval/__main__.py:50`). The six QA and PM fixtures target other roles.

## Fixtures

Twelve golden tasks are checked in (`roboco/eval/fixtures.py`).

Developer-role (the default `--fixtures` set):

1. `bugfix-off-by-one`
2. `bugfix-null-check`
3. `small-feature-greet`
4. `refactor-duplicate-normalize`
5. `docs-readme-flag`
6. `research-magic-constant`

Seeded-defect fixtures, `target_role` set to `qa` or `cell_pm`:

7. `qa-catch-off-by-one` (qa)
8. `pm-delegate-pagination-fix` (cell_pm)
9. `qa-catch-dropped-ac` (qa)
10. `qa-catch-conventions-misplacement` (qa)
11. `qa-catch-security-flaw` (qa)
12. `qa-catch-vacuous-test` (qa)

Each is a small seeded repo plus a brief and a checked-in expectation. They are authored fixtures, not sourced from real pull requests.

## How the run was attempted

Intended invocation:

```
python -m roboco.eval run --role be-dev-2 --cohort baseline-2026-11-03 \
  --json-out <path>
```

It could not run in this sandbox. Two blockers, both observed:

1. **No Docker.** `docker` is not installed in the be-dev-2 container (`docker: command not found`). Real spawn needs a Docker daemon and built agent images (`roboco/eval/__main__.py` docstring).
2. **Shell guard.** The agent bash guard denied running `roboco.*` modules from the shell ("importing or running roboco.* internals from the shell bypasses the MCP role manifest"). The command never started.

The repository's own test targets (`make test`, `make e2e-smoke`) are also Docker-backed or need the test Postgres, so the scripted-spawner fallback (`tests/e2e_smoke/test_eval_bench.py`) was not run either. Even if it had, it would prove runner plumbing only, not agent quality.

## Results

| Run id | Cohort | Fixtures | Scores |
|---|---|---|---|
| none | none | none | none |

No run id exists, so no figure is given.

## What this does and does not show

- It shows the harness exists, is invocable, and is gated on infrastructure this sandbox lacks.
- It does not show how any model scores on it.
- FrontierCode 1.1 uses 150 tasks from real open-source pull requests, 1,000+ reviewer-defined criteria, and blocking criteria. This harness has twelve authored fixtures (six developer-role, six QA/PM seeded-defect). The two are not comparable, and no comparison is made.

## Follow-up

- Run `python -m roboco.eval run` on a host with Docker and built agent images; record the cohort label, each fixture key, and the `--json-out` file as the receipt.
- Replace the empty table above with those receipts. Do not publish a figure that lacks one.
- Methodology differences are analysed in [frontiercode-1.1-gap-analysis.md](frontiercode-1.1-gap-analysis.md).
