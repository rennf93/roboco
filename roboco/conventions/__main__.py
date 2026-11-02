"""``python -m roboco.conventions check --root <dir> --files <a> <b> ...``.

Builds the effective map from the repo's ``.roboco/conventions.yml`` overlaid
on auto-derived defaults, runs the validator over the named files, and prints
one JSON finding per line. Exit 0 when it ran (findings may be empty or
present); exit 3 when it could not run (a broken config or a grammar failure),
with ``{"error": ...}`` on stderr — the fail-loud signal the gate blocks on.

``--fail-on block`` makes a run that found any block-level finding exit 1, so a
CI step can hold a pull request to the rules without parsing the JSON itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from roboco.foundation.policy.conventions.effective_map import effective_map
from roboco.foundation.policy.conventions.models import (
    ConventionsParseError,
    ConventionsStandard,
)

from .runner import ValidatorCouldNotRun, run
from .scan import derive_from_scan

_CONVENTIONS_FILE = ".roboco/conventions.yml"
_EXIT_FINDINGS_AT_FAIL_LEVEL = 1
_EXIT_COULD_NOT_RUN = 3


def _load_file(root: Path) -> ConventionsStandard | None:
    path = root / _CONVENTIONS_FILE
    if not path.is_file():
        return None
    return ConventionsStandard.parse_yaml(path.read_text())


def _fail(reason: str) -> int:
    print(json.dumps({"error": reason}), file=sys.stderr)
    return _EXIT_COULD_NOT_RUN


def _run_check(root: Path, files: list[str], fail_on: str | None) -> int:
    try:
        file_standard = _load_file(root)
    except ConventionsParseError as exc:
        return _fail(f"unparseable {_CONVENTIONS_FILE}: {exc.reason}")
    standard = effective_map(derive_from_scan(root), file_standard)
    try:
        findings = run(root, files, standard)
    except ValidatorCouldNotRun as exc:
        return _fail(str(exc))
    for finding in findings:
        print(finding.as_json())
    if fail_on is not None and any(finding.level == fail_on for finding in findings):
        return _EXIT_FINDINGS_AT_FAIL_LEVEL
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse args and run the requested command. Returns the process exit code."""
    parser = argparse.ArgumentParser(prog="python -m roboco.conventions")
    subcommands = parser.add_subparsers(dest="command", required=True)
    check = subcommands.add_parser("check", help="check changed files")
    check.add_argument("--root", required=True, type=Path)
    check.add_argument("--files", nargs="*", default=[])
    check.add_argument(
        "--fail-on",
        choices=("block",),
        default=None,
        help="exit 1 when any finding is at this level",
    )
    args = parser.parse_args(argv)
    return _run_check(args.root, list(args.files), args.fail_on)


if __name__ == "__main__":
    raise SystemExit(main())
