"""docker/scripts/entire-wrapper.sh gates the agent images' Entire CLI.

The roboco repo commits Entire's Claude Code hooks, so every agent calls
`entire` on each session event and edit. The wrapper reads
ROBOCO_ENTIRE_ENABLED (set at spawn from the entire_enabled flag): off, every
hook is a silent no-op; on, session hooks reach the real CLI while Brain stays
unavailable to the fleet. Each test runs a copy of the wrapper pointed at a
stand-in binary that records how it was called.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(__file__).parents[3] / "docker" / "scripts" / "entire-wrapper.sh"
_REAL_PATH_IN_IMAGE = "/usr/local/lib/entire/entire"


@pytest.fixture
def wrapper_with_recording_cli(tmp_path: Path) -> tuple[Path, Path]:
    calls = tmp_path / "calls.log"
    real = tmp_path / "real-entire"
    real.write_text(f'#!/bin/sh\necho "$*" >> {calls}\nexit 0\n')
    real.chmod(0o755)
    wrapper = tmp_path / "entire"
    wrapper.write_text(WRAPPER.read_text().replace(_REAL_PATH_IN_IMAGE, str(real)))
    wrapper.chmod(0o755)
    return wrapper, calls


def _run(
    wrapper: Path, args: list[str], enabled: str | None
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "ROBOCO_ENTIRE_ENABLED"}
    if enabled is not None:
        env["ROBOCO_ENTIRE_ENABLED"] = enabled
    return subprocess.run(
        [str(wrapper), *args], capture_output=True, text=True, env=env, check=False
    )


def _real_calls(calls: Path) -> list[str]:
    return calls.read_text().splitlines() if calls.exists() else []


def test_wrapper_path_matches_the_dockerfile_install_location() -> None:
    assert _REAL_PATH_IN_IMAGE in WRAPPER.read_text()


@pytest.mark.parametrize("enabled", [None, "false", "0", "TRUE"])
@pytest.mark.parametrize(
    "args",
    [
        ["hooks", "claude-code", "stop"],
        ["hooks", "git", "pre-push", "origin"],
        ["brain", "hook", "pre-edit", "--claude-hook"],
    ],
)
def test_flag_off_turns_every_hook_into_a_silent_noop(
    wrapper_with_recording_cli: tuple[Path, Path], enabled: str | None, args: list[str]
) -> None:
    wrapper, calls = wrapper_with_recording_cli
    result = _run(wrapper, args, enabled)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert _real_calls(calls) == []


def test_flag_off_refuses_other_commands_and_says_why(
    wrapper_with_recording_cli: tuple[Path, Path],
) -> None:
    wrapper, calls = wrapper_with_recording_cli
    result = _run(wrapper, ["status"], "false")
    assert result.returncode == 1
    assert "entire_enabled" in result.stderr
    assert _real_calls(calls) == []


def test_flag_on_passes_session_hooks_to_the_real_cli(
    wrapper_with_recording_cli: tuple[Path, Path],
) -> None:
    wrapper, calls = wrapper_with_recording_cli
    result = _run(wrapper, ["hooks", "claude-code", "session-start"], "true")
    assert result.returncode == 0
    assert _real_calls(calls) == ["hooks claude-code session-start"]


def test_flag_on_keeps_the_brain_edit_hook_a_noop(
    wrapper_with_recording_cli: tuple[Path, Path],
) -> None:
    wrapper, calls = wrapper_with_recording_cli
    result = _run(wrapper, ["brain", "hook", "pre-edit", "--claude-hook"], "true")
    assert (result.returncode, result.stderr) == (0, "")
    assert _real_calls(calls) == []


def test_flag_on_refuses_brain_commands_and_says_why(
    wrapper_with_recording_cli: tuple[Path, Path],
) -> None:
    wrapper, calls = wrapper_with_recording_cli
    result = _run(wrapper, ["brain", "brief", "some task", "--json"], "true")
    assert result.returncode == 1
    assert "Brain is not available" in result.stderr
    assert _real_calls(calls) == []
