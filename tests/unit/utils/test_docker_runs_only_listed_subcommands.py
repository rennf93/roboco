"""Structural guard: RoboCo runs only the docker subcommands it lists.

``roboco/utils/docker_subcommands.py`` lists every docker CLI subcommand the
package runs, so the list is RoboCo's whole Docker surface. This scan reads
every docker argv in ``roboco/`` and fails on a subcommand the list lacks, on
a listed one nothing runs any more, and on an argv whose subcommand it cannot
read, so a new call cannot get past by being built indirectly.

Where a docker argv is read:

* a ``["docker", ...]`` list or tuple, wherever it is built. That covers the
  agent spawn argv (``AgentOrchestrator._build_mount_args``, which every
  provider in ``roboco/llm/providers/`` extends with options and runs), the
  Intake and Secretary chats and the throwaway workspace container;
* ``create_subprocess_exec("docker", ...)``, the argv spread as arguments;
* the sandbox provisioner's runner, which puts ``docker`` in front of an argv
  that starts at the subcommand: its callers' argv is read instead, and
  reaching the runner by any other name fails.

A subcommand may be a literal, a conditional of literals, or a local name
bound to those. A ``"docker"`` string anywhere else (but a path join) fails
as well, so the binary cannot be renamed past the scan.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from roboco.utils.docker_subcommands import DOCKER_SUBCOMMANDS

_PACKAGE = Path(__file__).parents[3] / "roboco"
_SANDBOX = _PACKAGE / "runtime" / "sandbox.py"
# The docker CLI's management commands: the verb after one is part of the
# subcommand (``container inspect``, ``network create``).
_MANAGEMENT_COMMANDS = frozenset(
    {
        "builder",
        "buildx",
        "checkpoint",
        "compose",
        "config",
        "container",
        "context",
        "image",
        "manifest",
        "network",
        "node",
        "plugin",
        "secret",
        "service",
        "stack",
        "swarm",
        "system",
        "trust",
        "volume",
    }
)

_Scope = ast.Module | ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class _PassThroughRunner:
    """A function that puts ``docker`` in front of the argv its callers pass.

    ``callees`` are every expression the runner is reached by; ``getter`` is
    the one function allowed to hand it out without calling it.
    """

    function: str
    getter: str
    callees: frozenset[str]


_SANDBOX_RUNNER = _PassThroughRunner(
    function="_default_docker_run",
    getter="_run",
    callees=frozenset({"_default_docker_run", "self.runner", "self._run()", "run"}),
)


def _is_docker(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == "docker"


class _DockerArgvScan:
    """The docker subcommands one module's source runs, by line."""

    def __init__(
        self,
        source: str,
        *,
        runner: _PassThroughRunner | None = None,
    ) -> None:
        self._tree = ast.parse(source)
        self._runner = runner
        self._parents = {
            child: node
            for node in ast.walk(self._tree)
            for child in ast.iter_child_nodes(node)
        }
        self._bindings = self._local_bindings()
        self._heads: set[ast.expr] = set()
        self.subcommands: dict[str, list[int]] = defaultdict(list)
        self.unreadable: list[int] = []
        self._scan()
        if runner is not None:
            self.unreadable += self._runner_escapes(runner)
        self.unreadable.sort()
        for lines in self.subcommands.values():
            lines.sort()
        self.stray = sorted(
            node.lineno
            for node in ast.walk(self._tree)
            if isinstance(node, ast.Constant)
            and node.value == "docker"
            and node not in self._heads
            and not self._is_path_segment(node)
        )

    def _scope(self, node: ast.AST) -> _Scope:
        while node in self._parents:
            node = self._parents[node]
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                return node
        assert isinstance(node, ast.Module)
        return node

    def _local_bindings(self) -> dict[tuple[_Scope, str], list[ast.expr]]:
        bindings: dict[tuple[_Scope, str], list[ast.expr]] = defaultdict(list)
        for node in ast.walk(self._tree):
            if isinstance(node, ast.Assign):
                targets: list[ast.expr] = list(node.targets)
                value = node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
                value = node.value
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[(self._scope(node), target.id)].append(value)
        return bindings

    def _is_path_segment(self, node: ast.AST) -> bool:
        parent = self._parents.get(node)
        return isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div)

    def _words(
        self, node: ast.expr, scope: _Scope, seen: frozenset[str] = frozenset()
    ) -> set[str] | None:
        """The strings ``node`` can hold, or None when they are not all literal."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            body = self._words(node.body, scope, seen)
            orelse = self._words(node.orelse, scope, seen)
            return None if body is None or orelse is None else body | orelse
        if not isinstance(node, ast.Name) or node.id in seen:
            return None
        words: set[str] = set()
        bound = self._bindings.get((scope, node.id), [])
        for value in bound:
            value_words = self._words(value, scope, seen | {node.id})
            if value_words is None:
                return None
            words |= value_words
        return words or None

    def _argv_lists(self, node: ast.expr, scope: _Scope) -> list[ast.List | ast.Tuple]:
        if isinstance(node, ast.List | ast.Tuple):
            return [node]
        if not isinstance(node, ast.Name):
            return []
        bound = self._bindings.get((scope, node.id), [])
        if not all(isinstance(value, ast.List | ast.Tuple) for value in bound):
            return []
        return [value for value in bound if isinstance(value, ast.List | ast.Tuple)]

    @staticmethod
    def _is_function(scope: _Scope, name: str) -> bool:
        return not isinstance(scope, ast.Module) and scope.name == name

    def _is_runner(self, scope: _Scope) -> bool:
        return self._runner is not None and self._is_function(
            scope, self._runner.function
        )

    def _runner_escapes(self, runner: _PassThroughRunner) -> list[int]:
        """Lines that reach the runner other than by a call the scan reads."""
        escapes = []
        for node in ast.walk(self._tree):
            if not isinstance(node, ast.Name | ast.Attribute | ast.Call):
                continue
            if ast.unparse(node) not in runner.callees:
                continue
            parent = self._parents[node]
            called = isinstance(parent, ast.Call) and parent.func is node
            bound = isinstance(parent, ast.Assign) and all(
                isinstance(target, ast.Name) and target.id in runner.callees
                for target in parent.targets
            )
            handed_out = self._is_function(self._scope(node), runner.getter)
            if not (called or bound or handed_out):
                escapes.append(node.lineno)
        return escapes

    def _read(self, tail: list[ast.expr], scope: _Scope, line: int) -> None:
        """Record the subcommand ``tail`` (the argv after ``docker``) starts with."""
        if tail and isinstance(tail[0], ast.Starred) and self._is_runner(scope):
            return
        first = self._words(tail[0], scope) if tail else None
        if first is None or any(word.startswith("-") for word in first):
            self.unreadable.append(line)
            return
        for word in sorted(first):
            if word not in _MANAGEMENT_COMMANDS:
                self.subcommands[word].append(line)
                continue
            verbs = self._words(tail[1], scope) if len(tail) > 1 else None
            if verbs is None:
                self.unreadable.append(line)
                continue
            for verb in sorted(verbs):
                self.subcommands[f"{word} {verb}"].append(line)

    def _read_runner_call(self, call: ast.Call) -> None:
        scope = self._scope(call)
        argvs = self._argv_lists(call.args[0], scope) if call.args else []
        if not argvs:
            self.unreadable.append(call.lineno)
        for argv in argvs:
            self._read(argv.elts, scope, argv.lineno)

    def _scan(self) -> None:
        for node in ast.walk(self._tree):
            if isinstance(node, ast.List | ast.Tuple) and node.elts:
                argv = node.elts
            elif isinstance(node, ast.Call) and node.args:
                argv = node.args
                if self._runner and ast.unparse(node.func) in self._runner.callees:
                    self._read_runner_call(node)
                    continue
            else:
                continue
            if _is_docker(argv[0]):
                self._heads.add(argv[0])
                self._read(argv[1:], self._scope(node), node.lineno)


def _scan_module(path: Path) -> _DockerArgvScan:
    if path == _SANDBOX:
        return _DockerArgvScan(path.read_text(), runner=_SANDBOX_RUNNER)
    return _DockerArgvScan(path.read_text())


@cache
def _package_scan() -> tuple[dict[str, list[str]], list[str]]:
    """(subcommand -> sites, unreadable sites) over every module in ``roboco/``."""
    sites: dict[str, list[str]] = defaultdict(list)
    unreadable: list[str] = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        scan = _scan_module(path)
        where = path.relative_to(_PACKAGE.parent)
        for subcommand, lines in scan.subcommands.items():
            sites[subcommand] += [f"{where}:{line}" for line in lines]
        unreadable += [f"{where}:{line}" for line in scan.unreadable + scan.stray]
    return dict(sites), unreadable


def test_the_scan_reads_every_argv_shape() -> None:
    """Guards the guard: a scanner that went blind would pass everything."""
    source = """
import asyncio
asyncio.create_subprocess_exec("docker", "logs", name)
cmd: list[str] = ["docker", "run", "-d"]
asyncio.create_subprocess_exec("docker", "container", "inspect", name)
async def stop(graceful):
    verb = "stop" if graceful else "kill"
    await asyncio.create_subprocess_exec("docker", verb, name)
docker_dir = root / "docker"
"""
    scan = _DockerArgvScan(source)
    assert scan.subcommands == {
        "logs": [3],
        "run": [4],
        "container inspect": [5],
        "kill": [8],
        "stop": [8],
    }
    assert scan.unreadable == []
    assert scan.stray == []


def test_the_scan_refuses_a_subcommand_it_cannot_read() -> None:
    source = """
import asyncio, shutil
asyncio.create_subprocess_exec("docker", *argv)
argv = ["docker", subcommand, name]
argv = ["docker", "--host", host, "ps"]
asyncio.create_subprocess_exec("docker", "image")
DOCKER = "docker"
shutil.which("docker")
"""
    scan = _DockerArgvScan(source)
    assert scan.subcommands == {}
    assert scan.unreadable == [3, 4, 5, 6, 8]
    assert scan.stray == [7]


def test_the_scan_reads_the_sandbox_runner_through_its_callers() -> None:
    source = """
async def _default_docker_run(args, timeout):
    return await asyncio.create_subprocess_exec("docker", *args)
class Provisioner:
    def _run(self):
        return self.runner or _default_docker_run
    async def provision(self):
        run = self._run()
        await run(["image", "inspect", image], 1)
        args = ["run", "-d", "--name", name]
        await run(args, 1)
        await self._run()(["ps", "-a"], 1)
        await _default_docker_run(["pull", image], 1)
        await run(argv_from_elsewhere, 1)
        docker = self._run()
        await docker(["cp", src, dst], 1)
"""
    scan = _DockerArgvScan(source, runner=_SANDBOX_RUNNER)
    assert scan.subcommands == {
        "image inspect": [9],
        "run": [10],
        "ps": [12],
        "pull": [13],
    }
    assert scan.unreadable == [14, 15]
    assert _DockerArgvScan(source).unreadable == [3]


def test_every_docker_argv_names_its_subcommand() -> None:
    _, unreadable = _package_scan()
    assert unreadable == [], (
        f"{unreadable}: a docker argv this test cannot read. Build it as "
        '["docker", "<subcommand>", ...] with the subcommand as a literal'
    )


def test_every_docker_subcommand_run_is_listed() -> None:
    sites, _ = _package_scan()
    unlisted = {
        name: at for name, at in sites.items() if name not in DOCKER_SUBCOMMANDS
    }
    assert unlisted == {}, (
        f"{unlisted}: add each subcommand to DOCKER_SUBCOMMANDS in "
        "roboco/utils/docker_subcommands.py, RoboCo's whole Docker surface"
    )


def test_every_listed_docker_subcommand_is_still_run() -> None:
    sites, _ = _package_scan()
    assert DOCKER_SUBCOMMANDS - sites.keys() == set()


def test_inspect_always_names_its_object_type() -> None:
    """A bare ``docker inspect`` reports an image, network or volume of the
    name when no container has it."""
    assert "inspect" not in DOCKER_SUBCOMMANDS


def test_every_spawn_argv_builder_is_read() -> None:
    sites, _ = _package_scan()
    builders = {site.partition(":")[0] for site in sites["run"]}
    assert builders == {
        "roboco/runtime/engines/spawn_launch.py",
        "roboco/runtime/engines/interactive_sessions.py",
        "roboco/runtime/sandbox.py",
        "roboco/services/workspace_container.py",
    }
