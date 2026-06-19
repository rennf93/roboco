"""Render a Grok CLI agent's runtime config + per-role flags at container start.

The ``roboco-agent-grok-cli`` image's entrypoint runs ``python -m
roboco.llm.providers.grok_cli_config`` to turn the mounted Claude Code
``mcp-config.json`` into ``~/.grok/config.toml`` (``[mcp_servers]``) and to
compute the per-role ``grok -p`` flags (permissions / reasoning effort / turn
cap), written one token per line to an args file the entrypoint splices into the
command. Keeping the translation in importable Python (not a shell heredoc)
makes it unit-testable.

Parity with ``ClaudeCodeProvider``'s per-role permissions, expressed as native
grok flags instead of an opencode permission block + a JS guard plugin:

  * **subagents** — every role gets ``--disallowed-tools Agent``: no RoboCo agent
    spawns the CLI's own subagents (work is driven through the gateway verbs).
  * **editing** — roles that don't write code (``role_config.allows_write`` is
    False) have the edit tool removed (``--disallowed-tools search_replace``).
  * **shell** — roles that never run a shell (review / board) have bash removed
    (``--disallowed-tools run_terminal_cmd``).
  * **git mutation** — bash-capable roles keep a shell, but raw git mutation is
    denied (``--deny "Bash(git push*)"`` ...): agents commit / push through the
    gateway verbs, never raw git (the opencode secret-scrub git-ops rule, ported).
  * **destructive** — ``--deny "Bash(rm -rf*)"`` for every bash-capable role.
  * **reasoning** — ``--effort`` by role (``low`` for coordination / docs / board;
    full for the code-quality roles). A global ``ROBOCO_GROK_REASONING_EFFORT``
    override wins.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import tomli_w

from roboco.agents_config import get_agent_role
from roboco.services.gateway.role_config import get_role_config

# grok reads its global config from ``$HOME/.grok/config.toml`` (the agent's HOME
# is ``/home/agent``; the host ``auth.json`` is mounted alongside it).
GROK_CONFIG_PATH = Path.home() / ".grok" / "config.toml"
# The entrypoint reads the computed flags (one token per line) from this file.
GROK_ARGS_PATH = Path(os.environ.get("ROBOCO_GROK_ARGS_FILE", "/tmp/roboco-grok-args"))

# Hard ceiling on agentic turns (loop guard; replaces the opencode budget-feed
# loop cap). Operator-tunable.
_DEFAULT_MAX_TURNS = 200

# Roles that request reduced reasoning (grok bills reasoning at the output rate,
# so it dominates cost). Code-quality roles keep full reasoning; coordination /
# docs / board roles ask for ``low``.
_MINIMAL_REASONING_ROLES = frozenset(
    {
        "cell_pm",
        "main_pm",
        "documenter",
        "product_owner",
        "head_marketing",
        "auditor",
        "prompter",
        "secretary",
    }
)
_FULL_REASONING_OVERRIDES = frozenset({"default", "full", "none", ""})

# Roles that legitimately run a shell. Review / board roles never do.
_BASH_ROLES = frozenset({"developer", "documenter", "cell_pm", "main_pm"})

# Grok CLI tool IDs (from the CLI's --tools/--disallowed-tools reference).
_TOOL_SHELL = "run_terminal_cmd"
_TOOL_EDIT = "search_replace"
_TOOL_SUBAGENT = "Agent"

# Raw git mutation is gateway-mediated (the commit / open_pr verbs); agents never
# push or commit via raw bash. Denied for every bash-capable role.
_GIT_MUTATE_DENY = (
    "Bash(git push*)",
    "Bash(git fetch*)",
    "Bash(git pull*)",
    "Bash(git clone*)",
    "Bash(git commit*)",
    "Bash(git remote*)",
    "Bash(git reset*)",
)
_DESTRUCTIVE_DENY = ("Bash(rm -rf*)",)


def render_config_toml(mcp_config: dict[str, Any]) -> str:
    """Translate Claude Code ``mcpServers`` into grok's ``[mcp_servers]`` TOML.

    ``{"command": "uv", "args": [...], "env": {...}}`` becomes a
    ``[mcp_servers.<name>]`` table with the same fields. Returns an empty string
    when there are no servers (a config with no MCP block is valid).
    """
    servers: dict[str, dict[str, Any]] = {}
    for name, spec in (mcp_config.get("mcpServers") or {}).items():
        block: dict[str, Any] = {
            "command": str(spec.get("command", "")),
            "args": [str(a) for a in (spec.get("args") or [])],
        }
        env = spec.get("env") or {}
        if env:
            block["env"] = {str(k): str(v) for k, v in env.items()}
        servers[str(name)] = block
    return tomli_w.dumps({"mcp_servers": servers}) if servers else ""


def _allows_write(role: str) -> bool:
    """True if the role writes code (``role_config.allows_write``)."""
    try:
        return bool(get_role_config(role).allows_write)
    except KeyError:
        return False


def _disallowed_tools(role: str) -> str:
    """Comma-separated ``--disallowed-tools`` value for a role."""
    tools = [_TOOL_SUBAGENT]
    if role not in _BASH_ROLES:
        tools.append(_TOOL_SHELL)
    if not _allows_write(role):
        tools.append(_TOOL_EDIT)
    return ",".join(tools)


def _deny_rules(role: str) -> list[str]:
    """``--deny`` permission rules for a role (only bash-capable roles need any)."""
    if role not in _BASH_ROLES:
        return []  # bash removed entirely → nothing left to gate
    return [*_DESTRUCTIVE_DENY, *_GIT_MUTATE_DENY]


def _effort_for(role: str) -> str | None:
    """Resolve ``--effort`` for a role; ``None`` keeps grok's default reasoning.

    A global ``ROBOCO_GROK_REASONING_EFFORT`` override wins over the per-role
    default (``default`` / ``full`` / empty disables the reduction).
    """
    override = os.environ.get("ROBOCO_GROK_REASONING_EFFORT", "").strip()
    if override:
        return (
            None if override.lower() in _FULL_REASONING_OVERRIDES else override.lower()
        )
    return "low" if role in _MINIMAL_REASONING_ROLES else None


def grok_cli_args(agent_id: str, *, max_turns: int = _DEFAULT_MAX_TURNS) -> list[str]:
    """The per-role ``grok -p`` flag tokens for an agent (excludes ``-p``/model/cwd).

    Order: tool removal, turn cap, deny rules, then effort. Each token is a
    separate list element so the entrypoint can splice them without shell quoting.
    """
    role = get_agent_role(agent_id) or ""
    args: list[str] = ["--disallowed-tools", _disallowed_tools(role)]
    args += ["--max-turns", str(max_turns)]
    for rule in _deny_rules(role):
        args += ["--deny", rule]
    effort = _effort_for(role)
    if effort:
        args += ["--effort", effort]
    return args


def _load_mcp_config(path: str) -> dict[str, Any]:
    """Load the mounted mcp-config.json, tolerating a missing / invalid file."""
    try:
        with Path(path).open(encoding="utf-8") as fh:
            loaded = json.load(fh)
            return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> int:
    """Entrypoint: write ``~/.grok/config.toml`` + the per-role args file."""
    agent_id = os.environ.get("ROBOCO_AGENT_ID", "")
    mcp_path = os.environ.get("ROBOCO_MCP_CONFIG", "/app/mcp-config.json")
    try:
        max_turns = int(
            os.environ.get("ROBOCO_GROK_MAX_TURNS", str(_DEFAULT_MAX_TURNS))
        )
    except ValueError:
        max_turns = _DEFAULT_MAX_TURNS

    GROK_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    GROK_CONFIG_PATH.write_text(
        render_config_toml(_load_mcp_config(mcp_path)), encoding="utf-8"
    )
    GROK_ARGS_PATH.write_text(
        "\n".join(grok_cli_args(agent_id, max_turns=max_turns)) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
