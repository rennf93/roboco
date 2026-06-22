"""Smoke the real ``python -m roboco.conventions`` entrypoint as a subprocess.

Guards the contract the agent image depends on: the module runs, loads its
tree-sitter grammars, and emits JSONL findings with exit 0.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def test_cli_module_entrypoint_emits_jsonl(tmp_path: Path) -> None:
    routers = tmp_path / "app" / "routers"
    routers.mkdir(parents=True)
    (routers / "u.py").write_text(
        "from pydantic import BaseModel\nclass M(BaseModel):\n    x: int\n"
    )
    conv = tmp_path / ".roboco"
    conv.mkdir()
    (conv / "conventions.yml").write_text(
        "modules:\n  - path: app/routers\n    purpose: r\n    forbidden: [model]\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "roboco.conventions",
            "check",
            "--root",
            str(tmp_path),
            "--files",
            "app/routers/u.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.strip().splitlines() if line]
    assert lines
    assert json.loads(lines[0])["rule"] == "no_models_in_routers"
