"""CLI entrypoint for the eval bench.

    python -m roboco.eval run --role <slug> --cohort <name> \\
        [--fixtures a,b] [--json-out path]

NOT YET FUNCTIONAL: the real-spawn path (``OrchestratorStageSpawner``) is
deliberately cut — see ``roboco/eval/runner.py``'s module docstring's
"Real-spawn status" section — because a real container spawn's MCP wiring
would authenticate against the REAL production orchestrator, not this
harness's disposable one. ``run`` will raise ``NotImplementedError`` once it
reaches the first fixture. The only working path today is driving
``EvalRunner`` with an injected scripted ``StageSpawner`` from Python (see
``tests/e2e_smoke/test_eval_bench.py``); this CLI is wired for the day the
follow-up lands, not for use today.

Offline dev/ops tool: no panel surface, no feature flag. Also runs from a
source checkout only (needs ``tests/e2e_smoke``, not shipped in containers
or wheels) plus the test Postgres (``ROBOCO_TEST_DB_*`` env vars, mirroring
the rest of the gate toolchain).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from roboco.eval.fixtures import FIXTURES, BenchTaskSpec
from roboco.eval.runner import EvalRunner


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m roboco.eval")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser(
        "run",
        help=(
            "Replay the golden-task fixtures against one agent "
            "[NOT YET FUNCTIONAL — real-spawn path is cut, see module docstring]"
        ),
    )
    run.add_argument(
        "--role", required=True, help="Agent slug under test (e.g. be-dev-1)"
    )
    run.add_argument(
        "--cohort",
        required=True,
        help="Label for this run, for before/after comparison (e.g. baseline)",
    )
    run.add_argument(
        "--fixtures",
        default=None,
        help="Comma-separated fixture keys (default: every developer-role fixture)",
    )
    run.add_argument(
        "--json-out",
        default=None,
        type=Path,
        help="Write the scored cohort result as JSON to this path",
    )

    return parser.parse_args(argv)


def _select_fixtures(spec: str | None) -> tuple[list[BenchTaskSpec] | None, str | None]:
    """Resolve `--fixtures a,b` to a fixture list, or an error message for an
    unknown key. `(None, None)` means "no filter — run every fixture"."""
    if not spec:
        return None, None
    keys = {key.strip() for key in spec.split(",") if key.strip()}
    fixtures = [f for f in FIXTURES if f.key in keys]
    missing = keys - {f.key for f in fixtures}
    if missing:
        return None, f"Unknown fixture key(s): {sorted(missing)}"
    return fixtures, None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.command != "run":
        return 1

    fixtures, error = _select_fixtures(args.fixtures)
    if error:
        print(error, file=sys.stderr)
        return 2

    runner = EvalRunner()
    runner.run_cohort(args.role, args.cohort, fixtures=fixtures, json_out=args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
