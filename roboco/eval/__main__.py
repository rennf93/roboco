"""CLI entrypoint for the eval bench.

    python -m roboco.eval run --role <slug> --cohort <name> \\
        [--fixtures a,b] [--json-out path]

Replays the golden-task fixtures against one agent through the REAL delivery
lifecycle: ``OrchestratorStageSpawner`` drives a real ``AgentOrchestrator``
container spawn per turn, and ``_generate_mcp_config`` honors the patched
``settings.api_url`` so spawned MCP servers resolve to the harness's
disposable orchestrator, never the real production one. Needs a Docker daemon
+ built agent images. The injectable scripted ``StageSpawner`` (see
``tests/e2e_smoke/test_eval_bench.py``) remains the unit-test fallback that
proves the runner's plumbing without touching Docker.

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
        help="Replay the golden-task fixtures against one agent",
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
