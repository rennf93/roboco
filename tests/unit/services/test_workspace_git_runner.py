"""The workspace git runner: environment, privilege drop, overrides, audit."""

from __future__ import annotations

import pytest
from roboco.config import settings
from roboco.services import workspace_git as wg


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


def test_root_drops_to_the_git_user_with_no_groups_or_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wg.os, "geteuid", lambda: 0)
    monkeypatch.setattr(settings, "git_user_uid", 1500)
    monkeypatch.setattr(settings, "agent_gid", 1000)
    assert wg.workspace_user_argv(["git", "status"]) == [
        "setpriv",
        "--reuid=1500",
        "--regid=1000",
        "--clear-groups",
        "--no-new-privs",
        "--inh-caps=-all",
        "--",
        "git",
        "status",
    ]


def test_an_unprivileged_process_has_nothing_to_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wg.os, "geteuid", lambda: 1000)
    assert wg.workspace_user_argv(["git", "status"]) == ["git", "status"]


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


@pytest.mark.parametrize(("euid", "started"), [(0, "setpriv"), (1000, "git")])
def test_the_program_is_named_apart_from_its_arguments(
    monkeypatch: pytest.MonkeyPatch, euid: int, started: str
) -> None:
    """``executable`` is the program that starts; the arguments never are."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> object:
        calls.append((argv, kwargs))
        return object()

    monkeypatch.setattr(wg.os, "geteuid", lambda: euid)
    monkeypatch.setattr(wg.subprocess, "run", fake_run)
    wg.run_as_workspace_user("git", "status", "--", "-x", timeout=1)
    argv, kwargs = calls[0]
    assert kwargs["executable"] == started
    assert argv[0] == started
    assert argv[-4:] == ["git", "status", "--", "-x"]
