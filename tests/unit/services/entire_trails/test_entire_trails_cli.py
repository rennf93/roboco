"""The trails bridge's Entire CLI runner: argv only, the token in the
environment alone, a private home, and nothing of the token in what comes
back."""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

import pytest
from roboco.config import settings
from roboco.services.entire_trails import cli
from roboco.services.entire_trails.cli import EntireCliError, run_entire

if TYPE_CHECKING:
    from pathlib import Path

_TOKEN = "entire-test-token-5f1c"
_OWNER_ONLY = 0o700


def _fake_cli(tmp_path: Path, script: str) -> Path:
    binary = tmp_path / "entire"
    binary.write_text(f"#!/bin/sh\n{script}\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


@pytest.fixture
def private_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "private"
    monkeypatch.setattr(settings, "private_data_root", str(root))
    return root


async def test_args_reach_the_cli_verbatim_and_the_token_only_its_environment(
    tmp_path: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = tmp_path / "calls"
    binary = _fake_cli(
        tmp_path,
        f'for a in "$@"; do printf "%s\\n" "$a"; done > {calls}/argv\n'
        f'printf "%s" "$ENTIRE_TOKEN" > {calls}/token\n'
        f'printf "%s\\n%s\\n%s" "$HOME" "$PWD" "$PATH" > {calls}/env\n'
        "echo '{\"number\": 3}'",
    )
    calls.mkdir()
    monkeypatch.setattr(cli, "ENTIRE_CLI", str(binary))

    result = await run_entire(
        ["trail", "show", "3", "--title=-starts with a dash"], token=_TOKEN
    )

    assert result.json() == {"number": 3}
    assert (calls / "argv").read_text().splitlines() == [
        "trail",
        "show",
        "3",
        "--title=-starts with a dash",
    ]
    assert (calls / "token").read_text() == _TOKEN
    home, cwd, path = (calls / "env").read_text().splitlines()
    assert home == cwd == str(private_root / "entire-home")
    assert path == "/usr/local/bin:/usr/bin:/bin"
    assert stat.S_IMODE((private_root / "entire-home").stat().st_mode) == _OWNER_ONLY


async def test_a_failure_raises_with_the_clis_message_and_never_the_token(
    tmp_path: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = private_root
    binary = _fake_cli(
        tmp_path, f'echo "API error: bad request with {_TOKEN}" >&2\nexit 1'
    )
    monkeypatch.setattr(cli, "ENTIRE_CLI", str(binary))

    with pytest.raises(EntireCliError) as excinfo:
        await run_entire(["trail", "link", "3"], token=_TOKEN)

    message = str(excinfo.value)
    assert "entire trail link exited 1" in message
    assert "API error: bad request with <REDACTED>" in message
    assert _TOKEN not in message


async def test_the_token_is_scrubbed_from_successful_output_too(
    tmp_path: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = private_root
    binary = _fake_cli(tmp_path, f"echo '{json.dumps({'echo': _TOKEN})}'")
    monkeypatch.setattr(cli, "ENTIRE_CLI", str(binary))

    result = await run_entire(["trail", "show", "3"], token=_TOKEN)

    assert result.json() == {"echo": "<REDACTED>"}


async def test_a_hung_cli_is_killed_at_the_timeout(
    tmp_path: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = private_root
    binary = _fake_cli(tmp_path, "exec sleep 30")
    monkeypatch.setattr(cli, "ENTIRE_CLI", str(binary))

    with pytest.raises(EntireCliError, match=r"timed out after 0\.2s"):
        await run_entire(["trail", "show", "3"], token=_TOKEN, timeout=0.2)


async def test_no_token_means_no_call(
    tmp_path: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = private_root
    marker = tmp_path / "ran"
    binary = _fake_cli(tmp_path, f"touch {marker}")
    monkeypatch.setattr(cli, "ENTIRE_CLI", str(binary))

    with pytest.raises(EntireCliError, match="no Entire token"):
        await run_entire(["trail", "show", "3"], token="")

    assert not marker.exists()


async def test_printed_non_json_is_an_error(
    tmp_path: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = private_root
    binary = _fake_cli(tmp_path, "echo 'Updated finding X: open -> resolved'")
    monkeypatch.setattr(cli, "ENTIRE_CLI", str(binary))

    result = await run_entire(["trail", "finding", "resolve"], token=_TOKEN)

    with pytest.raises(EntireCliError, match="printed no JSON"):
        result.json()
