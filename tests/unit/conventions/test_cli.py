"""CLI: JSONL findings on stdout, exit 0 when it ran, exit 3 when it could not,
exit 1 when ``--fail-on block`` meets a block-level finding."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.conventions.__main__ import main
from roboco.conventions.runner import ValidatorCouldNotRun

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_EXIT_FINDINGS_AT_FAIL_LEVEL = 1
_EXIT_COULD_NOT_RUN = 3


def _seed_repo(root: Path) -> None:
    routers = root / "app" / "routers"
    routers.mkdir(parents=True)
    (routers / "u.py").write_text(
        "from pydantic import BaseModel\nclass M(BaseModel):\n    x: int\n"
    )
    conv = root / ".roboco"
    conv.mkdir()
    (conv / "conventions.yml").write_text(
        "modules:\n  - path: app/routers\n    purpose: r\n    forbidden: [model]\n"
    )


def test_cli_prints_jsonl_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_repo(tmp_path)
    rc = main(["check", "--root", str(tmp_path), "--files", "app/routers/u.py"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines
    assert json.loads(lines[0])["rule"] == "no_models_in_routers"


def test_cli_exits_zero_with_no_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "clean.py").write_text("def helper():\n    return 1\n")
    rc = main(["check", "--root", str(tmp_path), "--files", "clean.py"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_cli_exits_three_on_unparseable_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conv = tmp_path / ".roboco"
    conv.mkdir()
    (conv / "conventions.yml").write_text("modules: [oops\n")
    rc = main(["check", "--root", str(tmp_path), "--files"])
    assert rc == _EXIT_COULD_NOT_RUN
    assert "error" in capsys.readouterr().err


def test_cli_exits_three_when_validator_cannot_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "x.py").write_text("x = 1\n")

    def boom(*_args: object, **_kw: object) -> list:
        raise ValidatorCouldNotRun("no grammar")

    monkeypatch.setattr("roboco.conventions.__main__.run", boom)
    rc = main(["check", "--root", str(tmp_path), "--files", "x.py"])
    assert rc == _EXIT_COULD_NOT_RUN
    payload = json.loads(capsys.readouterr().err)
    assert "error" in payload


def test_cli_fail_on_block_exits_one_on_a_block_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_repo(tmp_path)
    rc = main(
        [
            "check",
            "--root",
            str(tmp_path),
            "--fail-on",
            "block",
            "--files",
            "app/routers/u.py",
        ]
    )
    assert rc == _EXIT_FINDINGS_AT_FAIL_LEVEL
    findings = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(finding["level"] == "block" for finding in findings)


def test_cli_fail_on_block_exits_zero_on_warn_only_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "noted.py").write_text("x = 1  # a trailing note\n")
    rc = main(
        ["check", "--root", str(tmp_path), "--fail-on", "block", "--files", "noted.py"]
    )
    assert rc == 0
    findings = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert findings
    assert {finding["level"] for finding in findings} == {"warn"}


def test_cli_without_fail_on_exits_zero_on_a_block_finding(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    rc = main(["check", "--root", str(tmp_path), "--files", "app/routers/u.py"])
    assert rc == 0
