"""Master's required checks report on every pull request, with the real result.

Master's ruleset (master-rule) requires six checks by name. A workflow that a
``paths:`` filter skips never reports its check, and a required check that
never reports blocks the PR, so the six workflows start on every pull request
and decide inside the job whether the PR touches their paths
(``.github/scripts/required_checks.py``). These tests pin each name to one
job, keep each job's paths match equal to its push trigger's paths, and check
that nothing the match skips can run anyway. They also cover the script
itself, including its diff against a real git merge commit.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
import yaml
from roboco.services.gateway.choreographer import pr_gate

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

# tests/unit/test_required_checks.py
#   parents[0] = unit
#   parents[1] = tests
#   parents[2] = <repo root>
ROOT = Path(__file__).parents[2]
_WORKFLOWS = ROOT / ".github" / "workflows"
_SCRIPT = ROOT / ".github" / "scripts" / "required_checks.py"

# master-rule's required status checks (repository ruleset 15278401), each with
# the workflow that must report it.
REQUIRED_CHECKS = {
    "ci": "ci.yml",
    "panel-ci": "panel-ci.yml",
    "code-ql": "code-ql.yml",
    "e2e-smoke": "e2e-smoke.yml",
    "codeql-js-ts": "codeql-js-ts.yml",
    "agent-image-smoke": "agent-image-smoke.yml",
}
# The workflows whose single job carries the required name and skips its own
# steps when the PR doesn't touch its paths.
_STEP_GATED = (
    "ci.yml",
    "panel-ci.yml",
    "code-ql.yml",
    "e2e-smoke.yml",
    "codeql-js-ts.yml",
)
_STEP_GATE = "steps.paths.outputs.relevant == 'true'"
_JOB_GATE = "needs.paths.outputs.relevant == 'true'"
# actions/checkout's fetch-depth: 0 fetches all history; otherwise the PR
# merge commit and its first parent take at least 2.
_FULL_HISTORY = 0
_MERGE_AND_BASE = 2
_USAGE_EXIT = 2
# The files each CodeQL workflow's language covers. CodeQL analyzes every such
# file in the checkout, whatever the workflow's paths say.
_CODEQL_LANGUAGE_FILES = {
    "code-ql.yml": (".py",),
    "codeql-js-ts.yml": (
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".html",
        ".htm",
        ".vue",
    ),
}


@cache
def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("required_checks", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def _workflow(name: str) -> dict[Any, Any]:
    return cast("dict[Any, Any]", yaml.safe_load((_WORKFLOWS / name).read_text()))


def _triggers(name: str) -> dict[str, Any]:
    # PyYAML parses the bare `on:` key as boolean True under YAML 1.1 rules.
    return cast("dict[str, Any]", _workflow(name)[True])


def _jobs(name: str) -> dict[str, dict[str, Any]]:
    return cast("dict[str, dict[str, Any]]", _workflow(name)["jobs"])


def _check_name(job_id: str, job: dict[str, Any]) -> str:
    return str(job.get("name", job_id))


def _steps_through_paths_match(name: str) -> list[dict[str, Any]]:
    """The steps of the job that runs the paths match, ending at that step."""
    for job in _jobs(name).values():
        steps = cast("list[dict[str, Any]]", job.get("steps", []))
        ids = [step.get("id") for step in steps]
        if "paths" in ids:
            return steps[: ids.index("paths") + 1]
    pytest.fail(f"{name} has no step with id 'paths'")


# ---- the workflows


def test_each_required_check_is_one_job_in_its_own_workflow() -> None:
    owners: dict[str, list[str]] = {check: [] for check in REQUIRED_CHECKS}
    for workflow in sorted(_WORKFLOWS.glob("*.yml")):
        for job_id, job in _jobs(workflow.name).items():
            check = _check_name(job_id, job)
            if check in owners:
                owners[check].append(workflow.name)
    assert owners == {check: [wf] for check, wf in REQUIRED_CHECKS.items()}


@pytest.mark.parametrize(("check", "workflow"), sorted(REQUIRED_CHECKS.items()))
def test_no_required_check_job_has_a_matrix(check: str, workflow: str) -> None:
    # GitHub appends a matrix job's values to its check name (the CodeQL jobs
    # reported `code-ql (python, none)`), so the bare required name would
    # never report.
    jobs = [
        job
        for job_id, job in _jobs(workflow).items()
        if _check_name(job_id, job) == check
    ]
    assert jobs
    assert all("matrix" not in job.get("strategy", {}) for job in jobs)


@pytest.mark.parametrize("workflow", sorted(set(REQUIRED_CHECKS.values())))
def test_pull_requests_start_the_workflow_whatever_they_touch(workflow: str) -> None:
    pull_request = _triggers(workflow)["pull_request"] or {}
    assert "paths" not in pull_request
    assert "paths-ignore" not in pull_request


@pytest.mark.parametrize("workflow", sorted(set(REQUIRED_CHECKS.values())))
def test_the_paths_match_lists_the_push_triggers_paths(workflow: str) -> None:
    step = _steps_through_paths_match(workflow)[-1]
    listed = [line for line in step["env"]["PATHS"].splitlines() if line.strip()]
    assert listed == _triggers(workflow)["push"]["paths"]
    assert step["env"]["EVENT"] == "${{ github.event_name }}"
    assert step["env"]["BASE_REF"] == "${{ github.base_ref }}"
    assert step["run"] == "python3 .github/scripts/required_checks.py match-paths"


@pytest.mark.parametrize("workflow", sorted(set(REQUIRED_CHECKS.values())))
def test_the_checkout_before_the_paths_match_reaches_the_base_commit(
    workflow: str,
) -> None:
    # The match diffs the PR merge commit against its first parent.
    checkouts = [
        step
        for step in _steps_through_paths_match(workflow)[:-1]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert len(checkouts) == 1
    depth = int(checkouts[0].get("with", {}).get("fetch-depth", 1))
    assert depth == _FULL_HISTORY or depth >= _MERGE_AND_BASE


@pytest.mark.parametrize("workflow", _STEP_GATED)
def test_every_step_after_the_paths_match_is_gated_on_it(workflow: str) -> None:
    ((_, job),) = _jobs(workflow).items()
    steps = cast("list[dict[str, Any]]", job["steps"])
    after = steps[len(_steps_through_paths_match(workflow)) :]
    assert after
    assert [step.get("if") for step in after] == [_STEP_GATE] * len(after)


@pytest.mark.parametrize("workflow", _STEP_GATED)
def test_the_concurrency_group_is_named_after_the_required_check(workflow: str) -> None:
    # A branch cut before the rename runs the old definition on push; sharing a
    # group with it would let a cancelled run strand its old-named check.
    ((job_id, job),) = _jobs(workflow).items()
    check = _check_name(job_id, job)
    group = _workflow(workflow)["concurrency"]["group"]
    assert group == f"check-{check}-${{{{ github.head_ref || github.ref_name }}}}"


def test_agent_image_smoke_reports_one_result_for_both_smoke_jobs() -> None:
    jobs = _jobs("agent-image-smoke.yml")
    smoke = [job_id for job_id in jobs if job_id not in {"paths", "agent-image-smoke"}]
    assert smoke
    for job_id in smoke:
        assert jobs[job_id]["needs"] == "paths"
        assert jobs[job_id]["if"] == _JOB_GATE
    assert jobs["paths"]["outputs"]["relevant"] == "${{ steps.paths.outputs.relevant }}"
    result = jobs["agent-image-smoke"]
    assert sorted(result["needs"]) == sorted(["paths", *smoke])
    assert result["if"] == "always()"
    verdict = result["steps"][-1]
    assert verdict["env"] == {"NEEDS": "${{ toJSON(needs) }}", "PATHS_JOB": "paths"}
    assert verdict["run"] == "python3 .github/scripts/required_checks.py verdict"


def test_agent_image_smoke_has_no_concurrency_group() -> None:
    # GitHub creates a needs-job's check run only once its needs finish. With a
    # concurrency group, a cancelled run's verdict would be the newest
    # `agent-image-smoke` check for the whole surviving run: failing under
    # always(), or skipped (which GitHub counts as passing) under !cancelled().
    assert "concurrency" not in _workflow("agent-image-smoke.yml")
    assert all(
        "concurrency" not in job for job in _jobs("agent-image-smoke.yml").values()
    )


@pytest.mark.parametrize("check", ["code-ql", "codeql-js-ts"])
def test_pr_gate_maps_each_codeql_check_to_the_paths_its_workflow_analyzes(
    check: str,
) -> None:
    workflow = REQUIRED_CHECKS[check]
    own_file = f".github/workflows/{workflow}"
    analyzed = tuple(p for p in _triggers(workflow)["push"]["paths"] if p != own_file)
    assert pr_gate._CODEQL_CHECK_SCOPES[check] == analyzed


@cache
def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return out.splitlines()


@pytest.mark.parametrize(
    ("workflow", "suffixes"), sorted(_CODEQL_LANGUAGE_FILES.items())
)
def test_every_file_codeql_analyzes_is_under_its_workflows_paths(
    workflow: str, suffixes: tuple[str, ...]
) -> None:
    # A change to a file outside the paths runs no analysis on its PR, so an
    # alert it adds reaches slave unseen (#1196's two motion/ alerts did), and
    # pr_gate's scope map, which mirrors the paths, would excuse it as out of
    # scope.
    patterns = [_script().github_glob(p) for p in _triggers(workflow)["push"]["paths"]]
    files = [f for f in _tracked_files() if f.endswith(suffixes)]
    assert files
    assert [f for f in files if not any(p.match(f) for p in patterns)] == []


# ---- the script


@pytest.mark.parametrize(
    ("pattern", "path", "matches"),
    [
        ("panel/**", "panel/src/app.tsx", True),
        ("panel/**", "panelx/app.tsx", False),
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        (".github/**.md", ".github/ISSUE_TEMPLATE/bug.md", True),
        (".github/**.md", ".github/workflows/ci.yml", False),
        ("Makefile", "Makefile", True),
        ("Makefile", "Makefile.bak", False),
    ],
)
def test_github_glob_reads_star_and_double_star_as_github_does(
    pattern: str, path: str, matches: bool
) -> None:
    assert bool(_script().github_glob(pattern).match(path)) is matches


@pytest.mark.parametrize("pattern", ["docs/?.md", "!panel/**", "[ab].md", "a+.md"])
def test_github_glob_refuses_syntax_it_cannot_read(pattern: str) -> None:
    with pytest.raises(ValueError, match="unsupported glob"):
        _script().github_glob(pattern)


def test_first_match_returns_the_file_and_the_pattern() -> None:
    first_match = _script().first_match
    assert first_match(["roboco/a.py", "panel/b.ts"], ["panel/**"]) == (
        "panel/b.ts",
        "panel/**",
    )
    assert first_match(["roboco/a.py"], ["panel/**"]) is None


def _never_diffed() -> list[str]:
    pytest.fail("decide_relevance diffed when it had no reason to")


@pytest.mark.parametrize("event", ["push", "schedule", "workflow_dispatch"])
def test_every_event_but_a_pull_request_runs_without_a_diff(event: str) -> None:
    relevant, _ = _script().decide_relevance(event, "", ["panel/**"], _never_diffed)
    assert relevant is True


def test_a_pull_request_into_master_always_runs_in_full() -> None:
    relevant, reason = _script().decide_relevance(
        "pull_request", "master", ["panel/**"], _never_diffed
    )
    assert relevant is True
    assert "master" in reason


@pytest.mark.parametrize(
    ("files", "relevant"),
    [(["roboco/a.py"], False), (["roboco/a.py", "panel/b.ts"], True), ([], False)],
)
def test_a_pull_request_into_slave_runs_only_when_a_file_matches(
    files: list[str], relevant: bool
) -> None:
    decided, reason = _script().decide_relevance(
        "pull_request", "slave", ["panel/**"], lambda: files
    )
    assert decided is relevant
    assert ("panel/b.ts matches panel/**" in reason) is relevant


def test_an_empty_paths_list_is_refused() -> None:
    with pytest.raises(ValueError, match="PATHS is empty"):
        _script().decide_relevance("push", "", [], _never_diffed)


def _needs(relevant: str | None, **smoke: str) -> dict[str, dict[str, Any]]:
    outputs = {} if relevant is None else {"relevant": relevant}
    return {
        "paths": {"result": "success", "outputs": outputs},
        **{job: {"result": result, "outputs": {}} for job, result in smoke.items()},
    }


@pytest.mark.parametrize(
    ("needs", "problems"),
    [
        (_needs("true", a="success", b="success"), []),
        (_needs("false", a="skipped", b="skipped"), []),
        (_needs("true", a="success", b="failure"), ["b: failure (expected success)"]),
        (_needs("true", a="skipped", b="success"), ["a: skipped (expected success)"]),
        (
            _needs("true", a="cancelled", b="success"),
            ["a: cancelled (expected success)"],
        ),
        (_needs("false", a="success", b="skipped"), ["a: success (expected skipped)"]),
        (
            _needs(None, a="skipped"),
            ["paths: output relevant=None (expected true or false)"],
        ),
        (
            {"paths": {"result": "failure", "outputs": {}}, "a": {"result": "skipped"}},
            ["paths: failure (expected success)"],
        ),
        ({"a": {"result": "success"}}, ["paths: None (expected success)"]),
    ],
)
def test_verdict_passes_only_when_every_job_ended_as_the_match_expects(
    needs: dict[str, dict[str, Any]], problems: list[str]
) -> None:
    assert _script().verdict_problems(needs, "paths") == problems


def test_match_paths_writes_the_decision_to_github_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("EVENT", "pull_request")
    monkeypatch.setenv("BASE_REF", "slave")
    monkeypatch.setenv("PATHS", "panel/**\n.github/workflows/panel-ci.yml\n")
    monkeypatch.setattr(_script(), "pr_changed_files", lambda: ["roboco/a.py"])
    assert _script().main(["required_checks.py", "match-paths"]) == 0
    assert output.read_text() == "relevant=false\n"


@pytest.mark.parametrize(
    ("needs", "code"),
    [(_needs("true", a="success"), 0), (_needs("true", a="failure"), 1)],
)
def test_verdict_exits_nonzero_on_a_problem(
    needs: dict[str, dict[str, Any]],
    code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("NEEDS", json.dumps(needs))
    monkeypatch.setenv("PATHS_JOB", "paths")
    assert _script().main(["required_checks.py", "verdict"]) == code
    assert ("::error::a: failure" in capsys.readouterr().out) is bool(code)


@pytest.mark.parametrize(
    "argv", [["required_checks.py"], ["required_checks.py", "nope"]]
)
def test_an_unknown_command_prints_usage(argv: list[str]) -> None:
    assert _script().main(argv) == _USAGE_EXIT


def _git(repo: Path) -> Callable[..., str]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    return run


def test_pr_changed_files_lists_what_the_merge_commit_changes_against_its_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mirror GitHub's pull_request checkout: a merge commit whose first parent
    # is the base tip, with the base having moved on since the branch was cut.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git = _git(tmp_path)
    git("init", "-q", "-b", "base")
    (tmp_path / "keep.txt").write_text("keep\n")
    (tmp_path / "old.txt").write_text("moves\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    git("switch", "-q", "-c", "topic")
    (tmp_path / "new").mkdir()
    git("mv", "old.txt", "new/renamed.txt")
    (tmp_path / "panel").mkdir()
    (tmp_path / "panel" / "x.ts").write_text("export {};\n")
    git("add", ".")
    git("commit", "-q", "-m", "topic")
    git("switch", "-q", "base")
    (tmp_path / "base-only.txt").write_text("landed on the base meanwhile\n")
    git("add", ".")
    git("commit", "-q", "-m", "base moves")
    git("merge", "-q", "--no-ff", "-m", "merge", "topic")
    monkeypatch.chdir(tmp_path)
    assert sorted(_script().pr_changed_files()) == [
        "new/renamed.txt",
        "old.txt",
        "panel/x.ts",
    ]
