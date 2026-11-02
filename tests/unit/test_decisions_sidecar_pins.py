""".github/workflows/decisions-sidecar.yml tests the decisions image that ships.

The workflow's llama.cpp and Laya-GGUF pins must equal the build args
docker/decisions.Dockerfile defaults to: fd290939 moved the Dockerfile to
llama.cpp b11371 (the first tag with the clef architecture) and left the
workflow on b11361, so CI validated a build that never shipped. A comment
saying "MUST stay in lockstep" did not prevent it; this test does.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

# tests/unit/test_decisions_sidecar_pins.py
#   parents[0] = unit
#   parents[1] = tests
#   parents[2] = <repo root>
ROOT = Path(__file__).parents[2]
_WORKFLOW = ROOT / ".github" / "workflows" / "decisions-sidecar.yml"
_DOCKERFILE = ROOT / "docker" / "decisions.Dockerfile"


def _workflow_env() -> dict[str, str]:
    doc = cast("dict[str, Any]", yaml.safe_load(_WORKFLOW.read_text()))
    return {key: str(value) for key, value in doc["env"].items()}


def _dockerfile_arg_default(name: str) -> str:
    match = re.search(
        rf"^ARG {name}=(\S+)$", _DOCKERFILE.read_text(), flags=re.MULTILINE
    )
    assert match is not None, f"no ARG {name}=<default> in {_DOCKERFILE.name}"
    return match.group(1)


@pytest.mark.parametrize("pin", ["LLAMA_CPP_REF", "LAYA_HF_REVISION"])
def test_workflow_pin_matches_the_dockerfile(pin: str) -> None:
    assert _workflow_env()[pin] == _dockerfile_arg_default(pin)
