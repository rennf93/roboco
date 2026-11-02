"""Let the checks master's ruleset requires report on every pull request.

Master's ruleset requires checks named ci, panel-ci, code-ql, e2e-smoke,
codeql-js-ts and agent-image-smoke. A workflow that a ``paths:`` filter skips
never reports its check, and a required check that never reports blocks the
PR, so those workflows trigger on every pull request and decide here whether
the PR touches their paths:

    python3 .github/scripts/required_checks.py match-paths

reads PATHS (the push trigger's paths, one per line), EVENT
(``github.event_name``) and BASE_REF (``github.base_ref``), and writes
``relevant=true`` or ``relevant=false`` to ``$GITHUB_OUTPUT``. A PR into
master always counts as relevant, so a release or a hotfix runs everything.

    python3 .github/scripts/required_checks.py verdict

serves a workflow whose required check is a result job over several jobs. It
reads NEEDS (``toJSON(needs)``) and PATHS_JOB (the id of the job that ran
match-paths), and exits 1 unless that job succeeded and every other job
passed (the PR is relevant) or was skipped (it is not).

Standard library only: it runs on the runner's system python3.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

# A PR into one of these branches runs every required workflow in full.
_FULL_RUN_BASES = frozenset({"master"})
_UNSUPPORTED_GLOB = frozenset("?+[]!")
# argv holds the script name and one command.
_EXPECTED_ARGC = 2
_RESULT_WHEN_RELEVANT = {"true": "success", "false": "skipped"}


def github_glob(pattern: str) -> re.Pattern[str]:
    """Compile a paths-filter pattern with GitHub's meaning of ``*`` and ``**``.

    ``*`` matches within one path segment and ``**`` across segments. The
    filters use no other glob syntax; refuse it rather than misread it.
    """
    if set(pattern) & _UNSUPPORTED_GLOB:
        msg = f"unsupported glob in {pattern!r}"
        raise ValueError(msg)
    parts = re.split(r"(\*\*|\*)", pattern)
    regex = "".join(
        ".*" if part == "**" else "[^/]*" if part == "*" else re.escape(part)
        for part in parts
    )
    return re.compile(rf"{regex}\Z")


def first_match(
    files: Iterable[str], patterns: Sequence[str]
) -> tuple[str, str] | None:
    """The first file any pattern matches, with that pattern."""
    compiled = [(pattern, github_glob(pattern)) for pattern in patterns]
    for path in files:
        for pattern, regex in compiled:
            if regex.match(path):
                return path, pattern
    return None


def pr_changed_files() -> list[str]:
    """Files the checked-out PR merge commit changes against its base.

    On ``pull_request`` the checkout is GitHub's merge commit, whose first
    parent is the base branch tip, so this needs ``fetch-depth`` of at least
    2. ``--no-renames`` lists a rename's old path as well as its new one.
    """
    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "HEAD^1", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.splitlines()


def decide_relevance(
    event: str,
    base_ref: str,
    patterns: Sequence[str],
    changed_files: Callable[[], list[str]],
) -> tuple[bool, str]:
    """Whether this run must do the workflow's work, and why."""
    if not patterns:
        msg = "PATHS is empty: the workflow must list its push trigger's paths"
        raise ValueError(msg)
    if event != "pull_request":
        return True, f"{event} event: its trigger's own filters already applied"
    if base_ref in _FULL_RUN_BASES:
        return True, f"PR into {base_ref}: every required workflow runs in full"
    files = changed_files()
    match = first_match(files, patterns)
    if match is None:
        return False, (
            f"none of the PR's {len(files)} changed files matches this workflow's paths"
        )
    return True, f"{match[0]} matches {match[1]}"


def verdict_problems(
    needs: Mapping[str, Mapping[str, object]], paths_job: str
) -> list[str]:
    """Why the result job must fail; empty when it passes."""
    matcher = needs.get(paths_job)
    if matcher is None or matcher.get("result") != "success":
        result = None if matcher is None else matcher.get("result")
        return [f"{paths_job}: {result} (expected success)"]
    outputs = matcher.get("outputs")
    relevant = outputs.get("relevant") if isinstance(outputs, dict) else None
    expected = _RESULT_WHEN_RELEVANT.get(str(relevant))
    if expected is None:
        return [f"{paths_job}: output relevant={relevant!r} (expected true or false)"]
    return [
        f"{job}: {need.get('result')} (expected {expected})"
        for job, need in needs.items()
        if job != paths_job and need.get("result") != expected
    ]


def _write_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def _match_paths() -> int:
    patterns = [
        line.strip() for line in os.environ["PATHS"].splitlines() if line.strip()
    ]
    relevant, reason = decide_relevance(
        os.environ["EVENT"],
        os.environ.get("BASE_REF", ""),
        patterns,
        pr_changed_files,
    )
    value = "true" if relevant else "false"
    print(f"relevant={value}: {reason}")
    _write_output("relevant", value)
    return 0


def _verdict() -> int:
    problems = verdict_problems(
        json.loads(os.environ["NEEDS"]), os.environ["PATHS_JOB"]
    )
    for problem in problems:
        print(f"::error::{problem}")
    if not problems:
        print("every needed job ended as expected")
    return 1 if problems else 0


def main(argv: Sequence[str]) -> int:
    commands = {"match-paths": _match_paths, "verdict": _verdict}
    command = commands.get(argv[1]) if len(argv) == _EXPECTED_ARGC else None
    if command is None:
        print("usage: required_checks.py match-paths|verdict", file=sys.stderr)
        return 2
    return command()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
