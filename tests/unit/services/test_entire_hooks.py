"""Entire's commit hooks: what counts as a session, a checkpoint, and its CLI."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from roboco.config import settings
from roboco.services.entire_hooks import (
    ENTIRE_CLI,
    checkpoint_refs,
    records_entire_sessions,
)
from roboco.services.git_origin import (
    OriginContext,
    UnsupportedOriginCommandError,
    push_checkpoint_refs,
)

_DOCKER = Path(__file__).parents[3] / "docker"
_ID = "01M62HNXJKAZ085RFT8EHNPF3W"


def test_checkpoint_ids_become_entires_refs_once_and_nothing_else_does() -> None:
    values = [
        _ID,
        _ID,
        "../../heads/main",
        _ID.lower(),
        _ID[:-1],
        "01M62HNXJKAZ085RFT8EHNPF3U",
    ]
    assert checkpoint_refs(values) == [f"refs/entire/checkpoints/3W/{_ID}"]


def test_a_session_counts_only_when_entire_is_on_and_one_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path))
    clone = tmp_path / "proj" / "backend" / "be-dev-1"
    sessions = clone / ".git" / "entire-sessions"
    monkeypatch.setattr(settings, "entire_enabled", True)
    assert not records_entire_sessions(clone)
    sessions.mkdir(parents=True)
    (sessions / "abc.lock").write_text("")
    assert not records_entire_sessions(clone)
    (sessions / "abc.json").write_text("{}")
    assert records_entire_sessions(clone)
    assert records_entire_sessions(clone / ".worktrees" / "t1")
    monkeypatch.setattr(settings, "entire_enabled", False)
    assert not records_entire_sessions(clone)


def _ctx(relay: MagicMock) -> OriginContext:
    return OriginContext(
        relay=relay,
        repo=Path("/data/workspaces/p/backend/a"),
        local_git=MagicMock(),
        from_workspace=True,
        timeout=30,
    )


def test_only_checkpoint_refs_are_pushed_by_this_path() -> None:
    relay = MagicMock()
    with pytest.raises(UnsupportedOriginCommandError):
        push_checkpoint_refs(_ctx(relay), ["refs/heads/main"])
    relay.take_ref.assert_not_called()
    relay.push_to_origin.assert_not_called()


def test_checkpoints_go_in_one_unforced_push_then_staging_refs_are_dropped() -> None:
    relay = MagicMock()
    relay.take_ref.side_effect = [
        (subprocess.CompletedProcess([], 0), "refs/roboco/incoming/a"),
        (subprocess.CompletedProcess([], 0), "refs/roboco/incoming/b"),
    ]
    relay.push_to_origin.return_value = subprocess.CompletedProcess([], 0)
    first = f"refs/entire/checkpoints/3W/{_ID}"
    other = "refs/entire/checkpoints/AW/01M620JW5RZ2G6QBKQAM0FZNAW"

    result = push_checkpoint_refs(_ctx(relay), [first, other])

    assert result.returncode == 0
    relay.push_to_origin.assert_called_once_with(
        [f"refs/roboco/incoming/a:{first}", f"refs/roboco/incoming/b:{other}"],
        timeout=30,
    )
    assert [c.args[0] for c in relay.drop_ref.call_args_list] == [
        "refs/roboco/incoming/a",
        "refs/roboco/incoming/b",
    ]


def test_a_checkpoint_that_cannot_be_taken_pushes_nothing() -> None:
    relay = MagicMock()
    relay.take_ref.side_effect = [
        (subprocess.CompletedProcess([], 0), "refs/roboco/incoming/a"),
        (
            subprocess.CompletedProcess([], 1, "", "fsck failed"),
            "refs/roboco/incoming/b",
        ),
    ]
    other = "refs/entire/checkpoints/AW/01M620JW5RZ2G6QBKQAM0FZNAW"

    result = push_checkpoint_refs(
        _ctx(relay), [f"refs/entire/checkpoints/3W/{_ID}", other]
    )

    assert result.returncode == 1
    relay.push_to_origin.assert_not_called()
    relay.drop_ref.assert_called_once_with("refs/roboco/incoming/a", timeout=30)


def _pins(dockerfile: str) -> dict[str, str]:
    text = (_DOCKER / dockerfile).read_text()
    return dict(re.findall(r"^ARG (ENTIRE_[A-Z0-9_]+)=(\S+)$", text, re.MULTILINE))


def test_the_orchestrator_image_pins_the_same_entire_as_the_agents() -> None:
    pins = _pins("orchestrator.Dockerfile")
    assert set(pins) == {"ENTIRE_VERSION", "ENTIRE_SHA256_AMD64", "ENTIRE_SHA256_ARM64"}
    assert pins == _pins("agent-base.Dockerfile")


def test_the_orchestrator_image_keeps_entire_off_path() -> None:
    text = (_DOCKER / "orchestrator.Dockerfile").read_text()
    assert f"COPY --from=entire {ENTIRE_CLI} {ENTIRE_CLI}" in text
    assert "/usr/local/bin/entire" not in text
    assert "/usr/local/lib/entire:" not in text
