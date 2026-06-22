"""End-to-end: the real validator subprocess feeds the real gate decision.

Exercises the whole enforcement path against a real repo on disk — effective
map (auto-derived ⊕ committed file), tree-sitter placement, waiver filtering,
and the gateway's block/pass decision — without the orchestrator plumbing:

1. a model defined in a router blocks the submit with the offending file:line;
2. after the model moves to ``app/models``, the submit passes;
3. a committed waiver lets a deliberately-kept model through the gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING, Any

from roboco.services.gateway.choreographer import Choreographer

if TYPE_CHECKING:
    from pathlib import Path

_MODEL_SRC = (
    "from pydantic import BaseModel\nclass UserCreate(BaseModel):\n    x: int\n"
)
_FORBID_MODEL = (
    "modules:\n  - path: app/routers\n    purpose: routes\n    forbidden: [model]\n"
)


def _run_validator(root: Path, files: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "roboco.conventions",
            "check",
            "--root",
            str(root),
            "--files",
            *files,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    findings = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return {"findings": findings, "could_not_run": proc.returncode != 0}


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_block_then_fix_then_waiver(tmp_path: Path) -> None:
    _write(tmp_path, ".roboco/conventions.yml", _FORBID_MODEL)
    _write(tmp_path, "app/routers/users.py", _MODEL_SRC)

    # 1. A Pydantic model in the router blocks, naming the offending file:line.
    blocked = _run_validator(tmp_path, ["app/routers/users.py"])
    rejection = Choreographer._conventions_rejection(blocked, {})
    assert rejection is not None
    assert "app/routers/users.py:2" in rejection.as_dict()["remediate"]

    # 2. Move the model to app/models; the router now only holds a route → passes.
    _write(
        tmp_path,
        "app/routers/users.py",
        "@router.get('/users')\ndef list_users():\n    return []\n",
    )
    _write(tmp_path, "app/models/user.py", _MODEL_SRC)
    fixed = _run_validator(tmp_path, ["app/routers/users.py", "app/models/user.py"])
    assert Choreographer._conventions_rejection(fixed, {}) is None

    # 3. A deliberately-kept model in a router blocks — until a committed waiver
    #    (reviewed in the PR) suppresses exactly that finding.
    _write(tmp_path, "app/routers/legacy.py", _MODEL_SRC)
    still_blocked = _run_validator(tmp_path, ["app/routers/legacy.py"])
    assert Choreographer._conventions_rejection(still_blocked, {}) is not None

    _write(
        tmp_path,
        ".roboco/conventions.yml",
        _FORBID_MODEL + "waivers:\n  - path: app/routers/legacy.py\n"
        "    rule: no_models_in_routers\n    reason: extraction tracked\n",
    )
    waived = _run_validator(tmp_path, ["app/routers/legacy.py"])
    assert Choreographer._conventions_rejection(waived, {}) is None
