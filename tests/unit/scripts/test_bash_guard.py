"""bash-guard-hook.sh denies curl/wget against orchestrator/localhost.

The PreToolUse hook reads Claude Code's event JSON from stdin in the form
``{"tool_name": "Bash", "tool_input": {"command": "..."}}`` and exits with
code 2 to deny, 0 to allow. Tests wrap each command in that envelope.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# tests/unit/scripts/test_bash_guard.py
#   parents[0] = scripts
#   parents[1] = unit
#   parents[2] = tests
#   parents[3] = <repo root>
GUARD = Path(__file__).parents[3] / "docker" / "scripts" / "bash-guard-hook.sh"

_DENIED = 2
_ALLOWED = 0


def _run(cmd: str) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    result = subprocess.run(
        [str(GUARD)],
        input=payload,
        capture_output=True,
        text=True,
    )
    return result.returncode


def test_blocks_internal_curl_to_orchestrator() -> None:
    assert (
        _run("curl http://roboco-orchestrator:8000/api/v2/flow/main_pm/delegate")
        == _DENIED
    )


def test_blocks_internal_curl_to_localhost() -> None:
    assert _run("curl http://localhost:8000/api/v2/flow/dev/i_am_done") == _DENIED


def test_blocks_internal_curl_to_127() -> None:
    assert _run("curl http://127.0.0.1:8000/api/health") == _DENIED


def test_allows_external_curl_to_documentation() -> None:
    assert _run("curl https://docs.python.org/3/") == _ALLOWED
