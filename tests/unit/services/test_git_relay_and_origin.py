"""The relay owns the token; origin commands are served through it."""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from roboco.config import settings
from roboco.services import git_relay
from roboco.services.git_origin import (
    OriginContext,
    UnsupportedOriginCommandError,
    is_origin_command,
    run_origin_command,
)
from roboco.services.git_relay import GitRelay, is_dispatcher_owned

_TOKEN = "ghp_UNITCANARY"
_GIT_FATAL = 128


def _done(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git"], returncode, stdout, "")


def test_the_token_rides_host_scoped_env_config_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "private_data_root", str(tmp_path))
    calls: list[dict[str, Any]] = []

    def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        return _done()

    monkeypatch.setattr(git_relay.subprocess, "run", _run)
    (tmp_path / "relays" / "p.git").mkdir(parents=True)
    (tmp_path / "relays" / "p.git" / "HEAD").write_text("ref: refs/heads/main\n")
    relay = GitRelay("p", "https://github.com/o/r.git", _TOKEN)

    relay.push_to_origin(["refs/heads/a:refs/heads/a"], timeout=5)

    (push,) = [c for c in calls if "push" in c["argv"]]
    basic = base64.b64encode(f"x-access-token:{_TOKEN}".encode()).decode()
    assert not any(_TOKEN in a or basic in a for a in push["argv"])
    assert push["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert push["env"]["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {basic}"
    assert "protocol.https.allow=always" in push["argv"]
    assert "core.hooksPath=/dev/null" in push["argv"]


def test_dispatcher_owned_means_under_the_private_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "private_data_root", "/data/private")
    assert is_dispatcher_owned(Path("/data/private/read/p"))
    assert not is_dispatcher_owned(Path("/data/workspaces/p/backend/a"))
    assert not is_dispatcher_owned(Path("/data/private/../workspaces/p"))


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["fetch", "origin", "main"], True),
        (["push", "-u", "origin", "b"], True),
        (["ls-remote", "--heads", "origin", "b"], True),
        (["pull", "--ff-only"], True),
        (["remote", "prune", "origin"], True),
        (["fetch", "/data/private/relays/p.git", "main"], False),
        (["status"], False),
    ],
)
def test_origin_commands_are_recognized(args: list[str], expected: bool) -> None:
    assert is_origin_command(args) is expected


def _ctx(local: Any, relay: Any) -> OriginContext:
    return OriginContext(
        relay=relay,
        repo=Path("/data/workspaces/p/t/a"),
        local_git=local,
        from_workspace=True,
        timeout=5,
    )


def test_a_branch_fetch_mirrors_into_the_relay_then_tracks_locally() -> None:
    relay = MagicMock(path=Path("/data/private/relays/p.git"))
    relay.fetch_from_origin.return_value = _done()
    local = MagicMock(return_value=_done())

    run_origin_command(
        _ctx(local, relay), ["fetch", "--no-tags", "origin", "b", "+pull/7/head:x"]
    )

    relay.fetch_from_origin.assert_called_once_with(
        ["+refs/heads/b:refs/heads/b", "+refs/pull/7/head:refs/pull/7/head"],
        prune=False,
        tags=False,
        timeout=5,
    )
    assert local.call_args.args[0][-3:] == [
        "/data/private/relays/p.git",
        "+refs/heads/b:refs/remotes/origin/b",
        "+refs/pull/7/head:refs/heads/x",
    ]


def test_a_failed_relay_fetch_is_returned_without_touching_the_clone() -> None:
    relay = MagicMock()
    relay.fetch_from_origin.return_value = subprocess.CompletedProcess(
        ["git"], _GIT_FATAL, "", "fatal: couldn't find remote ref nope"
    )
    local = MagicMock()
    result = run_origin_command(_ctx(local, relay), ["fetch", "origin", "nope"])
    assert result.returncode == _GIT_FATAL
    assert "couldn't find remote ref" in result.stderr
    local.assert_not_called()


def test_force_with_lease_expects_the_clones_tracking_ref() -> None:
    relay = MagicMock()
    relay.take_ref.return_value = (_done(), "refs/roboco/incoming/x")
    relay.push_to_origin.return_value = _done()
    shas = {"HEAD^{commit}": "aaa", "refs/remotes/origin/b": "bbb"}

    def _local(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["rev-parse", "--verify"]:
            return _done(shas[args[2]] + "\n")
        return _done()

    run_origin_command(
        _ctx(_local, relay), ["push", "--force-with-lease", "origin", "HEAD:b"]
    )

    relay.push_to_origin.assert_called_once_with(
        ["refs/roboco/incoming/x:refs/heads/b"], leases=["refs/heads/b:bbb"], timeout=5
    )
    relay.set_ref.assert_called_once_with("refs/heads/b", "aaa", timeout=5)
    relay.drop_ref.assert_called_once_with("refs/roboco/incoming/x", timeout=5)


def test_prune_drops_only_tracking_refs_gone_from_origin() -> None:
    relay = MagicMock()
    relay.ls_remote.return_value = _done("a1\trefs/heads/keep\n")
    deleted: list[str] = []

    def _local(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "for-each-ref":
            return _done(
                "refs/remotes/origin/HEAD\nrefs/remotes/origin/keep\n"
                "refs/remotes/origin/gone\n"
            )
        if args[:2] == ["update-ref", "-d"]:
            deleted.append(args[2])
        return _done()

    run_origin_command(_ctx(_local, relay), ["remote", "prune", "origin"])

    assert deleted == ["refs/remotes/origin/gone"]


def test_ls_remote_is_served_by_the_relay_with_its_patterns() -> None:
    relay = MagicMock()
    relay.ls_remote.return_value = _done("a1\trefs/heads/b\n")

    result = run_origin_command(
        _ctx(MagicMock(), relay), ["ls-remote", "--heads", "origin", "b"]
    )

    relay.ls_remote.assert_called_once_with(["--heads", "b"], timeout=5)
    assert result.stdout == "a1\trefs/heads/b\n"


def test_pull_is_a_relay_fetch_plus_a_fast_forward_merge() -> None:
    relay = MagicMock(path=Path("/r.git"))
    relay.fetch_from_origin.return_value = _done()
    seen: list[list[str]] = []

    def _local(args: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return _done("feat\n") if args[0] == "branch" else _done()

    run_origin_command(_ctx(_local, relay), ["pull", "--ff-only"])

    assert seen[-1] == ["merge", "--ff-only", "refs/remotes/origin/feat"]


@pytest.mark.parametrize(
    "args",
    [
        ["fetch", "--depth=1", "origin", "b"],
        ["push", "--force", "origin", "b"],
        ["push", "origin", "refs/tags/v1"],
        ["pull", "--rebase"],
        ["ls-remote", "--exit-code", "origin", "b"],
        ["fetch", "upstream", "b"],
    ],
)
def test_commands_roboco_never_issues_are_refused(args: list[str]) -> None:
    with pytest.raises(UnsupportedOriginCommandError):
        run_origin_command(_ctx(MagicMock(), SimpleNamespace()), args)
