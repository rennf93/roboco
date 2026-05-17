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


# ---------------------------------------------------------------------------
# Task #165: git-ops check must inspect commands, not file CONTENT.
# A file whose body documents git verbs (README, notes, a heredoc) is data
# the shell writes — not a git invocation. It must NOT be denied. But a real
# git command (including inside `bash -c "..."`, which IS executed) must
# still be denied — that is the hook's entire reason to exist.
# ---------------------------------------------------------------------------


def test_allows_heredoc_readme_documenting_git_verbs() -> None:
    """The exact smoke-13 wedge: restoring a README via heredoc whose body
    explains `git commit` / `git push`. The body is data, not commands."""
    cmd = (
        "cat > README.md << 'EOF'\n"
        "# Project\n"
        "Run `git commit -m msg` to save your work.\n"
        "Then `git push` to publish.\n"
        "EOF"
    )
    assert _run(cmd) == _ALLOWED


def test_allows_unquoted_heredoc_documenting_git() -> None:
    cmd = (
        "cat > docs/setup.md <<EOF\n"
        "git clone the repo, then git checkout -b feature.\n"
        "EOF"
    )
    assert _run(cmd) == _ALLOWED


def test_allows_dash_heredoc_documenting_git() -> None:
    """`<<-DELIM` indents the closing delimiter; body still stripped."""
    cmd = "cat > n.md <<-EOF\n\tgit rebase main then git push --force\n\tEOF"
    assert _run(cmd) == _ALLOWED


def test_allows_echo_writing_git_instructions_to_file() -> None:
    assert _run('echo "remember to git commit and git push" >> notes.md') == _ALLOWED


def test_allows_printf_writing_git_instructions() -> None:
    assert _run("printf 'git merge then git reset --hard\\n' > steps.txt") == _ALLOWED


def test_allows_python_writing_file_content_mentioning_git() -> None:
    """Non-roboco python that writes a string containing git verbs to a
    file. Not a roboco import (so #164 is irrelevant) and not a git call."""
    assert _run("python3 -c \"open('r.md','w').write('git push to ship')\"") == _ALLOWED


def test_still_denies_real_git_push() -> None:
    """Regression guard: the actual command must still be blocked."""
    assert _run("git push origin feature/backend/ABC12345") == _DENIED


def test_still_denies_git_in_bash_c_string() -> None:
    """The hook's core purpose (per its header): a compound command whose
    first token is `cd` but which executes `git fetch`. The quoted string
    is EXECUTED — not a heredoc/echo body — so it must NOT be skeletonized
    away."""
    assert _run('bash -c "cd /workspace && git fetch origin"') == _DENIED


def test_still_denies_git_commit_after_cd() -> None:
    assert _run("cd /workspace && git commit -m 'x'") == _DENIED


def test_still_denies_git_after_echo_separator() -> None:
    """echo's args are stripped, but the `&&` boundary is preserved so the
    real `git push` after it is still seen."""
    assert _run('echo "starting" && git push') == _DENIED


def test_still_denies_printf_piped_into_git_apply_path() -> None:
    """printf body stripped, but `| git checkout` survives the separator."""
    assert _run("printf 'patch' | git checkout -- .") == _DENIED


# ---------------------------------------------------------------------------
# Task #175: interpreter/library-driven HTTP to an internal host. The
# curl/wget rule only fires when the first token is an HTTP CLI; smoke-17
# reached the orchestrator with forged X-Agent-* headers via a python3
# heredoc using httpx. Close it language-agnostically.
# ---------------------------------------------------------------------------


def test_blocks_smoke17_python_httpx_heredoc_to_orchestrator() -> None:
    """The exact smoke-17 bypass: python3 heredoc, httpx.post to the
    orchestrator with hand-forged identity headers."""
    cmd = (
        "python3 << 'PYEOF'\n"
        "import httpx\n"
        'httpx.post("http://roboco-orchestrator:8000/api/v2/flow/'
        'developer/i_will_work_on",\n'
        '           headers={"X-Agent-ID": "00000000-0000-0000-0001-'
        '000000000001", "X-Agent-Role": "developer"})\n'
        "PYEOF"
    )
    assert _run(cmd) == _DENIED


def test_blocks_python_requests_to_localhost() -> None:
    assert (
        _run("python3 -c \"import requests; requests.get('http://localhost:8000/x')\"")
        == _DENIED
    )


def test_blocks_python_urllib_to_orchestrator() -> None:
    assert (
        _run(
            'python3 -c "import urllib.request; '
            "urllib.request.urlopen('http://roboco-orchestrator:8000/api')\""
        )
        == _DENIED
    )


def test_blocks_node_fetch_to_internal_host() -> None:
    assert (
        _run("node -e \"fetch('http://roboco-orchestrator:8000/api/v2/do/note')\"")
        == _DENIED
    )


def test_blocks_ruby_nethttp_to_127() -> None:
    assert (
        _run(
            "ruby -e \"require 'net/http'; Net::HTTP.get(URI('http://127.0.0.1:8000/x'))\""
        )
        == _DENIED
    )


def test_blocks_aiohttp_to_orchestrator() -> None:
    cmd = (
        "uv run python3 << 'EOF'\n"
        "import aiohttp, asyncio\n"
        "async def m():\n"
        "    async with aiohttp.ClientSession() as s:\n"
        '        await s.post("http://roboco-orchestrator:8000/api/v2/flow/'
        'developer/i_am_done")\n'
        "asyncio.run(m())\n"
        "EOF"
    )
    assert _run(cmd) == _DENIED


def test_allows_python_requests_to_external_host() -> None:
    """External HTTP (pypi/docs) has no internal host — must still pass."""
    assert (
        _run("python3 -c \"import requests; requests.get('https://pypi.org/simple/')\"")
        == _ALLOWED
    )


def test_allows_python_httpx_import_without_internal_host() -> None:
    """Importing/using an HTTP client with no internal host is fine —
    don't over-block normal dependency usage."""
    assert _run('python3 -c "import httpx; print(httpx.__version__)"') == _ALLOWED


def test_allows_pytest_even_if_suite_uses_requests() -> None:
    """The command string is just the runner — no http-client token and
    no internal host literal — so it must pass."""
    assert _run("uv run python -m pytest tests/unit/ -q") == _ALLOWED
