"""The workspace git runner: environment, privilege drop, overrides, audit."""

from __future__ import annotations

import os
import signal
import subprocess
import tarfile
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from roboco.config import settings
from roboco.services import workspace_git as wg

if TYPE_CHECKING:
    from pathlib import Path

_DEADLINE = 30
_GRACE = 10
_MARGIN = 5
_GROUP_WRITABLE_UMASK = 0o002
_REFUSED = 126
_TIMED_OUT = 124


def test_the_environment_is_built_from_scratch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "s3cret")
    monkeypatch.setenv("ROBOCO_DATABASE_PASSWORD", "pw")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'http.extraheader'='x'")
    env = wg.workspace_git_env()
    assert not [k for k in env if k.startswith("ROBOCO_")]
    assert "GIT_CONFIG_PARAMETERS" not in env
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert env["HOME"] == "/nonexistent"


def test_the_drop_runs_the_helper_under_an_in_command_timeout(
    helper: str,
) -> None:
    assert wg.workspace_user_argv(["git", "status"], timeout=30) == [
        helper,
        "timeout",
        "-k",
        "10s",
        "30s",
        "git",
        "status",
    ]


def test_with_the_drop_off_the_command_runs_as_this_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "workspace_git_drop", "off")
    monkeypatch.setattr(wg, "GIT_USER_HELPER", "/nonexistent/as-git-user")
    assert wg.workspace_user_argv(["git", "status"], timeout=_DEADLINE) == [
        "git",
        "status",
    ]
    assert wg.workspace_user_executable("git") == "git"
    assert wg.workspace_user_wait_seconds(_DEADLINE) == _DEADLINE


def test_overrides_pin_hooks_programs_and_transports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "git_support_dir", "/opt/roboco/git")
    pairs = wg.workspace_git_overrides()[1::2]
    assert pairs[0] == "core.hooksPath=/opt/roboco/git/hooks"
    for pinned in (
        "core.fsmonitor=false",
        "core.sshCommand=false",
        "credential.helper=",
        "commit.gpgSign=false",
        "protocol.allow=never",
        "protocol.file.allow=always",
        "protocol.https.allow=never",
        "protocol.ext.allow=never",
        "submodule.recurse=false",
    ):
        assert pinned in pairs
    assert set(wg.workspace_git_overrides()[::2]) == {"-c"}


@pytest.mark.parametrize("verb", ["diff", "log", "show"])
def test_diff_verbs_never_run_external_diff_or_textconv(verb: str) -> None:
    argv = wg.workspace_git_argv([verb, "HEAD"])
    tail = argv[len(argv) - 4 :]
    assert tail == [verb, "--no-ext-diff", "--no-textconv", "HEAD"]


def test_other_verbs_are_passed_through() -> None:
    assert wg.workspace_git_argv(["status", "--porcelain"])[-2:] == [
        "status",
        "--porcelain",
    ]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("core.repositoryformatversion", "allowed"),
        ("remote.origin.url", "allowed"),
        ("branch.feature/x.merge", "allowed"),
        ("user.email", "allowed"),
        ("core.hooksPath", "neutralized"),
        ("core.fsmonitor", "neutralized"),
        ("credential.helper", "neutralized"),
        ("protocol.ext.allow", "neutralized"),
        ("http.proxy", "neutralized"),
        ("filter.lfs.process", "dangerous"),
        ("filter.evil.clean", "dangerous"),
        ("diff.evil.textconv", "dangerous"),
        ("diff.external", "dangerous"),
        ("merge.evil.driver", "dangerous"),
        ("url.https://evil/.insteadof", "dangerous"),
        ("include.path", "dangerous"),
        ("includeIf.gitdir:/x.path", "dangerous"),
        ("remote.origin.uploadpack", "dangerous"),
        ("core.worktree", "dangerous"),
        ("core.alternateRefsCommand", "dangerous"),
        ("branch.main.vscode-merge-base", "unknown"),
        ("diff.renames", "unknown"),
    ],
)
def test_config_keys_are_classified(key: str, expected: str) -> None:
    assert wg.classify_git_config_key(key) == expected


@pytest.mark.parametrize("drop", ["required", "off"])
def test_the_program_is_named_apart_from_its_arguments(
    monkeypatch: pytest.MonkeyPatch, helper: str, drop: str
) -> None:
    """``executable`` is the program that starts; the arguments never are."""
    monkeypatch.setattr(settings, "workspace_git_drop", drop)
    started = helper if drop == "required" else "git"
    popen = _FakePopen.install(monkeypatch)
    wg.run_as_workspace_user("git", "status", "--", "-x", timeout=1)
    argv, kwargs = popen.calls[0]
    assert kwargs["executable"] == started
    assert argv[0] == started
    assert argv[-4:] == ["git", "status", "--", "-x"]
    assert kwargs["umask"] == _GROUP_WRITABLE_UMASK


# --------------------------------------------------------------------------- #
# The drop fails closed
# --------------------------------------------------------------------------- #


def test_a_missing_helper_refuses_and_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_git_drop", "required")
    monkeypatch.setattr(wg, "GIT_USER_HELPER", str(tmp_path / "missing"))
    popen = _FakePopen.install(monkeypatch)
    with pytest.raises(wg.GitUserDropError, match="missing or not executable"):
        wg.workspace_user_argv(["git", "status"], timeout=1)
    with pytest.raises(wg.GitUserDropError):
        wg.run_as_workspace_user("git", "status", timeout=1)
    with pytest.raises(wg.GitUserDropError):
        wg.verify_git_user_drop()
    assert popen.calls == []


@pytest.mark.usefixtures("helper")
def test_a_refusing_helper_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakePopen.install(
        monkeypatch,
        _Outcome(returncode=126, stderr="as-git-user: the set-id bits did not\n"),
    )
    with pytest.raises(wg.GitUserDropError, match="set-id bits"):
        wg.run_as_workspace_user("git", "status", timeout=1)


@pytest.mark.parametrize(
    ("drop", "stderr"),
    [("required", "timeout: failed to run command 'git'\n"), ("off", "as-git-user: x")],
)
@pytest.mark.usefixtures("helper")
def test_the_command_own_126_is_its_result(
    monkeypatch: pytest.MonkeyPatch, drop: str, stderr: str
) -> None:
    monkeypatch.setattr(settings, "workspace_git_drop", drop)
    _FakePopen.install(monkeypatch, _Outcome(returncode=126, stderr=stderr))
    result = wg.run_as_workspace_user("git", "status", timeout=1)
    assert result.returncode == _REFUSED


@pytest.mark.parametrize(
    ("timeout", "seconds"), [(0, "1s"), (0.2, "1s"), (30, "30s"), (30.5, "31s")]
)
@pytest.mark.usefixtures("helper")
def test_the_in_command_timeout_is_whole_seconds_and_never_zero(
    timeout: float, seconds: str
) -> None:
    """``timeout 0`` would never end the command."""
    assert wg.workspace_user_argv(["true"], timeout=timeout)[4] == seconds


@pytest.mark.usefixtures("helper")
def test_the_caller_waits_past_the_deadline_and_the_kill_grace() -> None:
    assert wg.workspace_user_wait_seconds(_DEADLINE) == _DEADLINE + _GRACE + _MARGIN


@pytest.mark.parametrize("returncode", [124, -signal.SIGKILL])
@pytest.mark.usefixtures("helper")
def test_the_in_command_timeout_raises_timeout_expired(
    monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    _FakePopen.install(monkeypatch, _Outcome(returncode=returncode, elapsed=_DEADLINE))
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        wg.run_as_workspace_user("git", "fetch", timeout=_DEADLINE)
    assert raised.value.timeout == _DEADLINE


@pytest.mark.parametrize(
    ("drop", "elapsed"), [("required", 29.0), ("off", 30.0)], ids=["early", "off"]
)
@pytest.mark.usefixtures("helper")
def test_an_exit_124_is_a_timeout_only_from_the_in_command_timeout(
    monkeypatch: pytest.MonkeyPatch, drop: str, elapsed: float
) -> None:
    monkeypatch.setattr(settings, "workspace_git_drop", drop)
    _FakePopen.install(monkeypatch, _Outcome(returncode=_TIMED_OUT, elapsed=elapsed))
    result = wg.run_as_workspace_user("git", "fetch", timeout=_DEADLINE)
    assert result.returncode == _TIMED_OUT


@pytest.mark.usefixtures("helper")
def test_a_child_this_process_may_not_kill_is_left_to_its_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-root parent gets EPERM killing a git-user child: the wait still
    ends with ``TimeoutExpired``, never ``PermissionError`` or a hang."""
    popen = _FakePopen.install(
        monkeypatch, _Outcome(hangs=True, kill_error=PermissionError())
    )
    with pytest.raises(subprocess.TimeoutExpired):
        wg.run_as_workspace_user("git", "fetch", timeout=_DEADLINE)
    process = popen.started[0]
    assert process.killed
    assert process.waits == [_DEADLINE + _GRACE + _MARGIN]
    assert process.reaps == [_GRACE]


def test_the_real_timeout_ends_a_command_that_ignores_term(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GNU ``timeout`` behind a pass-through helper: TERM is ignored, KILL
    after the grace ends it, and the caller sees ``TimeoutExpired``."""
    _pass_through_helper(monkeypatch, tmp_path)
    monkeypatch.setattr(wg, "_KILL_GRACE_SECONDS", 1)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        wg.run_as_workspace_user("sh", "-c", "trap '' TERM; sleep 30", timeout=1)
    assert time.monotonic() - started < 1 + 1 + 5


def test_the_archive_runs_through_the_drop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pass_through_helper(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    repo = tmp_path / "repo"
    git = ["git", "-c", "user.name=s", "-c", "user.email=s@s", "-C", str(repo)]
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "f").write_text("x\n")
    subprocess.run([*git, "add", "f"], check=True)
    subprocess.run([*git, "commit", "-qm", "c"], check=True)
    archive = tmp_path / "out.tar.gz"

    ok, stderr = wg.archive_workspace_ref(repo, "HEAD", archive)

    assert ok, stderr
    with tarfile.open(archive) as tar:
        assert tar.getnames() == ["f"]


# --------------------------------------------------------------------------- #
# Startup self-test
# --------------------------------------------------------------------------- #

_GOOD_STATUS = {
    "Uid": "1500\t1500\t1500\t1500",
    "Gid": "1000\t1000\t1000\t1000",
    "Groups": "",
    "NoNewPrivs": "1",
    "CapInh": "0000000000000000",
    "CapPrm": "0000000000000000",
    "CapEff": "0000000000000000",
    "CapAmb": "0000000000000000",
}


def _status(**changes: str) -> str:
    fields = _GOOD_STATUS | changes
    return "Name:\tcat\n" + "".join(f"{k}:\t{v}\n" for k, v in fields.items())


@pytest.mark.parametrize("groups", ["", "1000"])
@pytest.mark.usefixtures("helper")
def test_the_self_test_accepts_the_git_user(
    monkeypatch: pytest.MonkeyPatch, groups: str
) -> None:
    popen = _FakePopen.install(monkeypatch, _Outcome(stdout=_status(Groups=groups)))
    wg.verify_git_user_drop()
    assert popen.calls[0][0][-2:] == ["cat", "/proc/self/status"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Uid", "0\t1500\t1500\t1500"),
        ("Gid", "1000\t1000\t0\t1000"),
        ("Groups", "1000 999"),
        ("NoNewPrivs", "0"),
        ("CapInh", "0000000000000400"),
        ("CapPrm", "00000000a80425fb"),
        ("CapEff", "00000000a80425fb"),
        ("CapAmb", "0000000000000400"),
    ],
)
@pytest.mark.usefixtures("helper")
def test_the_self_test_refuses_anything_more(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    _FakePopen.install(monkeypatch, _Outcome(stdout=_status(**{field: value})))
    with pytest.raises(wg.GitUserDropError, match=field):
        wg.verify_git_user_drop()


@pytest.mark.usefixtures("helper")
def test_the_self_test_refuses_a_failed_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakePopen.install(monkeypatch, _Outcome(returncode=1, stderr="cat: denied"))
    with pytest.raises(wg.GitUserDropError, match="exited 1"):
        wg.verify_git_user_drop()


def test_the_self_test_is_a_no_op_with_the_drop_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "workspace_git_drop", "off")
    popen = _FakePopen.install(monkeypatch)
    wg.verify_git_user_drop()
    assert popen.calls == []


# --------------------------------------------------------------------------- #
# The descriptor-based paths follow the setting, not the euid
# --------------------------------------------------------------------------- #


def test_with_the_drop_off_the_workspace_paths_are_plain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_git_drop", "off")
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path))
    monkeypatch.setattr(wg.os, "geteuid", lambda: 0)
    monkeypatch.setattr(wg, "_open_workspace_dir", _never)
    monkeypatch.setattr(wg, "make_clone_group_writable", _never)
    clone = tmp_path / "proj" / "backend" / "be-dev-1"

    wg.prepare_clone_parent(clone)
    assert clone.parent.is_dir()
    assert wg._repair_clone_permissions(clone) is False
    wg.remove_workspace_tree(clone.parent)
    assert not clone.parent.exists()


def test_with_the_drop_required_the_workspace_paths_go_by_descriptor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_git_drop", "required")
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path))
    monkeypatch.setattr(wg.os, "geteuid", lambda: 1000)
    opened: list[tuple[Path, bool]] = []
    repaired: list[Path] = []

    def fake_open(directory: Path, *, create: bool) -> int:
        opened.append((directory, create))
        return os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)

    monkeypatch.setattr(wg, "_open_workspace_dir", fake_open)
    monkeypatch.setattr(wg, "make_clone_group_writable", repaired.append)
    clone = tmp_path / "proj" / "backend" / "be-dev-1"

    wg.prepare_clone_parent(clone)
    assert wg._repair_clone_permissions(clone) is True
    wg.remove_workspace_tree(clone, ignore_errors=True)

    assert opened == [(clone.parent, True), (clone.parent, False)]
    assert repaired == [clone]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def helper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """The drop required, with an executable file standing in for the helper."""
    path = tmp_path / "as-git-user"
    path.write_text("#!/bin/sh\nexit 99\n")
    path.chmod(0o755)
    monkeypatch.setattr(settings, "workspace_git_drop", "required")
    monkeypatch.setattr(wg, "GIT_USER_HELPER", str(path))
    return str(path)


def _pass_through_helper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The drop required, with a helper that runs its argv as this user."""
    path = tmp_path / "as-git-user"
    path.write_text('#!/bin/sh\nexec "$@"\n')
    path.chmod(0o755)
    monkeypatch.setattr(settings, "workspace_git_drop", "required")
    monkeypatch.setattr(wg, "GIT_USER_HELPER", str(path))


def _never(*_args: object, **_kwargs: object) -> int:
    raise AssertionError("the descriptor path ran with the drop off")


@dataclass
class _Outcome:
    """What a faked workspace-user process does."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    elapsed: float = 0.0
    hangs: bool = False
    kill_error: BaseException | None = None


class _FakeProcess:
    def __init__(self, popen: _FakePopen) -> None:
        self.popen = popen
        self.returncode: int | None = None
        self.killed = False
        self.waits: list[float | None] = []
        self.reaps: list[float | None] = []
        self.stdin = self.stdout = self.stderr = None

    def communicate(
        self, _input: object = None, timeout: float | None = None
    ) -> tuple[str, str]:
        outcome = self.popen.outcome
        self.waits.append(timeout)
        if outcome.hangs:
            raise subprocess.TimeoutExpired("x", timeout or 0)
        self.popen.clock += outcome.elapsed
        self.returncode = outcome.returncode
        return outcome.stdout, outcome.stderr

    def wait(self, timeout: float | None = None) -> int:
        self.reaps.append(timeout)
        raise subprocess.TimeoutExpired("x", timeout or 0)

    def kill(self) -> None:
        self.killed = True
        if self.popen.outcome.kill_error is not None:
            raise self.popen.outcome.kill_error


class _FakePopen:
    def __init__(self, outcome: _Outcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.started: list[_FakeProcess] = []
        self.clock = 1000.0

    @classmethod
    def install(
        cls, monkeypatch: pytest.MonkeyPatch, outcome: _Outcome | None = None
    ) -> _FakePopen:
        popen = cls(outcome or _Outcome())
        monkeypatch.setattr(wg.subprocess, "Popen", popen)
        monkeypatch.setattr(wg.time, "monotonic", lambda: popen.clock)
        return popen

    def __call__(self, argv: list[str], **kwargs: object) -> _FakeProcess:
        self.calls.append((argv, kwargs))
        process = _FakeProcess(self)
        self.started.append(process)
        return process
