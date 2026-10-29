"""Structural guard: git runs only through the two hardened runners.

``roboco/services/workspace_git.py`` (agent workspaces, as the git user) and
``roboco/services/git_relay.py`` (dispatcher-owned repositories and origin,
as root) are the only modules that may start a git process, and the relay is
the only module that may turn a project token into git credentials. A new
``subprocess.run(["git", ...])`` anywhere else would quietly reopen the hole
those modules close: root git in an agent-writable clone, or a token in reach
of one.

Two scans: a git command handed to a process spawner (``subprocess``,
``asyncio``, or the generic git-user wrappers, which skip the runner's
repository pinning and config audit), and any ``["git", ...]`` argv built
anywhere — so an argv assembled in a variable and spawned later is caught
where it is built. A git argv that is only a result label
(``CompletedProcess`` / ``CalledProcessError``) or runs in the throwaway
workspace container (the agent uid, no secret, no RoboCo network) is fine.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TypeGuard

import pytest

_PACKAGE = Path(__file__).parents[3] / "roboco"
_RUNNERS = {
    _PACKAGE / "services" / "workspace_git.py",
    _PACKAGE / "services" / "git_relay.py",
}
_TOKEN_MODULE = _PACKAGE / "services" / "git_relay.py"
_SPAWNERS = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "create_subprocess_exec",
        "create_subprocess_shell",
        "run_as_workspace_user",
        "workspace_user_argv",
    }
)
# Calls whose git argv is not a git process started here.
_RESULT_LABELS = frozenset({"CompletedProcess", "CalledProcessError"})
_SANDBOXED = frozenset({"run_in_workspace_container"})
# The Codex sandbox's command-prefix policy table: ("git", "push"), ... rules.
_NOT_SPAWNED = {_PACKAGE / "llm" / "providers" / "codex_cli_config.py"}


def _python_files() -> list[Path]:
    return sorted(_PACKAGE.rglob("*.py"))


def _first_arg_is_git(call: ast.Call) -> bool:
    if not call.args:
        return False
    first = call.args[0]
    if isinstance(first, ast.Constant):
        return first.value == "git"
    if isinstance(first, ast.List) and first.elts:
        head = first.elts[0]
        return isinstance(head, ast.Constant) and head.value == "git"
    return False


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _spawns_git(source: str) -> list[int]:
    tree = ast.parse(source)
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) in _SPAWNERS and _first_arg_is_git(node):
            lines.append(node.lineno)
    return lines


def _is_git_argv(node: ast.AST) -> TypeGuard[ast.List | ast.Tuple]:
    if not isinstance(node, ast.List | ast.Tuple) or not node.elts:
        return False
    head = node.elts[0]
    return isinstance(head, ast.Constant) and head.value == "git"


def _builds_git_argv(source: str) -> list[int]:
    tree = ast.parse(source)
    exempt = {
        id(arg)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_name(node) in _RESULT_LABELS | _SANDBOXED
        for arg in node.args
    }
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if _is_git_argv(node) and id(node) not in exempt
    )


def test_the_scan_catches_every_spawn_shape() -> None:
    """Guards the guard: a scanner that went blind would pass everything."""
    source = """
import asyncio, subprocess
subprocess.run(["git", "status"])
subprocess.check_output(["git", "log"])
asyncio.create_subprocess_exec("git", "fetch")
subprocess.run(["make", "gate"])
"""
    assert _spawns_git(source) == [3, 4, 5]


def test_the_scan_catches_git_argv_built_ahead_of_the_spawn() -> None:
    source = """
import subprocess
GIT = ("git", "-c", "x=y")
argv = ["git", "status"]
subprocess.run([*GIT, "log"])
run_as_workspace_user("git", *argv[1:])
subprocess.CompletedProcess(["git", "fetch"], 0, "", "")
raise subprocess.CalledProcessError(1, ["git", "push"])
run_in_workspace_container(cwd, ["git", "status"], timeout=1)
"""
    assert _builds_git_argv(source) == [3, 4]
    assert _spawns_git('run_as_workspace_user("git", "status")') == [1]


@pytest.mark.parametrize(
    "path", [p for p in _python_files() if p not in _RUNNERS], ids=str
)
def test_no_module_outside_the_runners_spawns_git(path: Path) -> None:
    source = path.read_text()
    found = _spawns_git(source)
    if path not in _NOT_SPAWNED:
        found += _builds_git_argv(source)
    assert found == [], (
        f"{path.relative_to(_PACKAGE.parent)} lines {sorted(set(found))} start "
        "or build a git command; use run_workspace_git (agent workspaces) or "
        "run_private_git / GitRelay (dispatcher-owned repositories) instead"
    )


_CREDENTIAL_MARKERS = ("http.extraheader", "x-access-token:")


def _code_strings(source: str) -> list[str]:
    """Every string constant in ``source`` except docstrings."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_only_the_relay_builds_git_credentials() -> None:
    offenders = [
        str(path.relative_to(_PACKAGE.parent))
        for path in _python_files()
        if path != _TOKEN_MODULE
        and any(
            marker in text
            for text in _code_strings(path.read_text())
            for marker in _CREDENTIAL_MARKERS
        )
    ]
    assert offenders == []


def test_the_credential_scan_ignores_docstrings_only() -> None:
    source = (
        '"""Docs may say http.extraheader."""\nHEADER = "x-access-token:" + token\n'
    )
    assert _code_strings(source) == ["x-access-token:"]
