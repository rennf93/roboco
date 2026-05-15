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
        check=False,
    )
    return result.returncode


def test_blocks_internal_curl_to_orchestrator() -> None:
    assert (
        _run("curl http://roboco-orchestrator:8000/api/v2/flow/main_pm/delegate")
        == _DENIED
    )


def test_blocks_internal_curl_to_localhost() -> None:
    assert _run("curl http://localhost:8000/api/v2/flow/developer/i_am_done") == _DENIED


def test_blocks_internal_curl_to_127() -> None:
    assert _run("curl http://127.0.0.1:8000/api/health") == _DENIED


def test_blocks_scheme_less_curl_to_orchestrator() -> None:
    assert _run("curl roboco-orchestrator:8000/api") != _ALLOWED


def test_blocks_scheme_less_curl_to_localhost() -> None:
    assert _run("curl localhost:8000/api") != _ALLOWED


def test_blocks_scheme_less_curl_to_127() -> None:
    assert _run("curl 127.0.0.1:8000/api") != _ALLOWED


def test_allows_external_curl_to_documentation() -> None:
    assert _run("curl https://docs.python.org/3/") == _ALLOWED


def test_github_url_still_uses_github_specific_deny() -> None:
    """Existing GitHub deny rule must fire BEFORE the new gateway deny."""
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "curl https://api.github.com/user"},
        }
    )
    result = subprocess.run(
        [str(GUARD)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != _ALLOWED
    # The GitHub-specific message should appear, not the gateway message.
    combined = (result.stdout + result.stderr).lower()
    assert "github" in combined or "pat" in combined


# ---------------------------------------------------------------------------
# Task #164: gateway-internals import bypass + agent-identity forgery
# ---------------------------------------------------------------------------


def test_blocks_uv_run_python_importing_flow_server() -> None:
    """The exact smoke-12 bypass: uv run python -c importing the flow server."""
    assert (
        _run(
            'uv run python3 -c "from roboco.mcp.flow_server import open_pr; '
            "open_pr(task_id='x')\""
        )
        == _DENIED
    )


def test_blocks_plain_python_c_import_roboco() -> None:
    assert _run('python3 -c "import roboco.services.gateway as g; g.foo()"') == _DENIED


def test_blocks_python_heredoc_importing_roboco() -> None:
    """Heredoc body is part of the command string — must still be caught."""
    cmd = (
        "uv run python3 << 'EOF'\n"
        "import os\n"
        "from roboco.mcp.do_server import commit\n"
        "commit(message='x')\n"
        "EOF"
    )
    assert _run(cmd) == _DENIED


def test_blocks_python_m_roboco_module() -> None:
    assert _run("python -m roboco.mcp.flow_server") == _DENIED
    assert _run("uv run -m roboco.services.gateway") == _DENIED


def test_blocks_poetry_run_python_import_roboco() -> None:
    assert _run('poetry run python -c "from roboco.runtime import x"') == _DENIED


def test_blocks_setting_roboco_agent_id_inline() -> None:
    """Forging identity via an inline env assignment before a command."""
    assert (
        _run(
            "ROBOCO_AGENT_ID=00000000-0000-0000-0001-000000000001 "
            "uv run python3 -c 'print(1)'"
        )
        == _DENIED
    )


def test_blocks_export_roboco_agent_id() -> None:
    assert (
        _run("export ROBOCO_AGENT_ID=00000000-0000-0000-0001-000000000001") == _DENIED
    )


def test_blocks_os_environ_roboco_agent_id_in_python() -> None:
    """The smoke-12 form: os.environ['ROBOCO_AGENT_ID']=... then import roboco.

    Caught by the import-bypass rule (references roboco import) even
    independent of the identity rule."""
    cmd = (
        'uv run python3 -c "import os; '
        "os.environ['ROBOCO_AGENT_ID']='00000000-0000-0000-0001-000000000001'; "
        "from roboco.mcp.flow_server import open_pr; open_pr(task_id='x')\""
    )
    assert _run(cmd) == _DENIED


def test_allows_legitimate_python_without_roboco() -> None:
    """A normal python one-liner that doesn't touch roboco internals or
    the identity var must still pass — don't over-block."""
    assert _run('python3 -c "print(2 + 2)"') == _ALLOWED


def test_allows_reading_roboco_source_with_cat() -> None:
    """Reading source files for context (cat/grep) is fine — the block is
    specifically on *executing* roboco internals, not viewing them."""
    assert _run("cat roboco/services/gateway/choreographer/_impl.py") == _ALLOWED


def test_allows_grep_for_roboco_symbol() -> None:
    assert _run("grep -rn 'import roboco' tests/") == _ALLOWED
