"""Render a Grok CLI agent's runtime config + per-role flags at container start.

The ``roboco-agent-grok-cli`` image's entrypoint runs ``python -m
roboco.llm.providers.grok_cli_config`` to turn the mounted Claude Code
``mcp-config.json`` into ``~/.grok/config.toml`` (``[mcp_servers]``) and to
compute the per-role ``grok -p`` flags (permissions / reasoning effort / turn
cap), written one token per line to an args file the entrypoint splices into the
command. Keeping the translation in importable Python (not a shell heredoc)
makes it unit-testable.

Parity with ``ClaudeCodeProvider``'s per-role permissions, expressed as native
grok flags (built-in tool removal + ``--deny`` rules):

  * **subagents** — every role gets ``--disallowed-tools Agent``: no RoboCo agent
    spawns the CLI's own subagents (work is driven through the gateway verbs).
  * **editing** — roles that don't write code (``role_config.allows_write`` is
    False) have the edit tool removed (``--disallowed-tools search_replace``).
  * **shell** — roles that never run a shell (review / board) have bash removed
    (``--disallowed-tools run_terminal_cmd``).
  * **git mutation** — bash-capable roles keep a shell, but raw git mutation is
    denied (``--deny "Bash(git push*)"`` ...): agents commit / push through the
    gateway verbs, never raw git.
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
# grok loads ``$HOME/.grok/AGENTS.md`` as a GLOBAL instruction file regardless of
# --cwd (verified live; the --system-prompt-override / --rules flags are ignored
# in headless mode). This is how the RoboCo role blueprint becomes grok's system
# prompt — the parity analogue of the Claude path's --system-prompt-file — without
# writing into (and polluting) the agent's git workspace.
GROK_AGENTS_PATH = Path.home() / ".grok" / "AGENTS.md"
# The composed role blueprint the orchestrator mounts into every agent container.
SYSTEM_PROMPT_PATH = Path(
    os.environ.get("ROBOCO_SYSTEM_PROMPT", "/app/system-prompt.md")
)
# grok loads blocking ``PreToolUse`` hooks from ``$HOME/.grok/hooks/*.json``
# (always trusted). We install the SAME bash-guard the Claude path runs as a
# PreToolUse hook to get its full exfil-pattern analysis (credential files,
# /proc/environ, internal-API forgery, identity forgery, …) — far beyond the
# glob ``--deny`` rules. It runs with ``ROBOCO_GUARD_SKIP_GIT=1``: a grok hook
# deny CANCELS the run, which is the right response for an exfil attempt (no
# legit use) but wrong for a routine git op, so git stays on the graceful
# ``--deny`` rules. The script is baked into the agent base image.
GROK_HOOKS_DIR = Path.home() / ".grok" / "hooks"
BASH_GUARD_HOOK = os.environ.get(
    "ROBOCO_BASH_GUARD_HOOK", "/app/scripts/bash-guard-hook.sh"
)
# The entrypoint reads the computed flags (one token per line) from this file.
GROK_ARGS_PATH = Path(os.environ.get("ROBOCO_GROK_ARGS_FILE", "/tmp/roboco-grok-args"))

# Hard ceiling on agentic turns (loop guard). Operator-tunable.
_DEFAULT_MAX_TURNS = 200

# Reasoning effort is left at grok's model default for every role (parity with
# the Claude path, which sets no per-role thinking budget). An operator can still
# trade quality for cost across the fleet with ``ROBOCO_GROK_REASONING_EFFORT``.
_FULL_REASONING_OVERRIDES = frozenset({"default", "full", "none", ""})

# Roles that legitimately run a shell. Review / board roles never do.
_BASH_ROLES = frozenset({"developer", "documenter", "cell_pm", "main_pm"})

# The intake interviewer reads the codebase to draft a task and may fan out
# exploration to subagents (parity with the Claude intake's ``Task`` allowance);
# every other role drives work through the gateway verbs, never CLI subagents.
_SUBAGENT_ALLOWED_ROLES = frozenset({"prompter"})

# Grok CLI tool IDs (from the CLI's --tools/--disallowed-tools reference).
_TOOL_SHELL = "run_terminal_cmd"
_TOOL_EDIT = "search_replace"
_TOOL_SUBAGENT = "Agent"

# Raw git network / branch / history mutation is gateway-mediated (the commit /
# open_pr verbs); agents never run these via raw bash. Denied for every
# bash-capable role — the same set the Claude bash-guard blocks.
#
# Git ops are GRACEFUL native ``--deny`` denials (a blocked command returns a
# permission error to the model, which adapts to the gateway verb — the run
# continues), deliberately NOT routed through the bash-guard hook: verified live
# that a grok hook deny CANCELS the whole run, which would turn one reflexive git
# op into a dropped task. The exfil categories (credential reads, /proc/environ,
# internal-API forgery, …) DO run through the hook (see GROK_HOOKS_DIR) — there a
# hard cancel is the right response, since no legitimate agent triggers them.
_GIT_MUTATE_DENY = (
    "Bash(git push*)",
    "Bash(git fetch*)",
    "Bash(git pull*)",
    "Bash(git clone*)",
    "Bash(git commit*)",
    "Bash(git remote*)",
    "Bash(git reset*)",
    "Bash(git ls-remote*)",
    "Bash(git checkout*)",
    "Bash(git merge*)",
    "Bash(git rebase*)",
    "Bash(git cherry-pick*)",
    "Bash(git revert*)",
    "Bash(git update-ref*)",
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
    tools: list[str] = []
    if role not in _SUBAGENT_ALLOWED_ROLES:
        tools.append(_TOOL_SUBAGENT)
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


def _effort() -> str | None:
    """Resolve ``--effort`` from the fleet override; ``None`` = grok's default.

    No per-role reduction (parity with Claude). A global
    ``ROBOCO_GROK_REASONING_EFFORT`` lets an operator dial cost vs quality;
    ``default`` / ``full`` / empty keeps the model default.
    """
    override = os.environ.get("ROBOCO_GROK_REASONING_EFFORT", "").strip()
    if override and override.lower() not in _FULL_REASONING_OVERRIDES:
        return override.lower()
    return None


def grok_cli_args_for_role(
    role: str, *, max_turns: int = _DEFAULT_MAX_TURNS
) -> list[str]:
    """The per-role ``grok -p`` flag tokens (excludes ``-p``/model/cwd).

    Order: tool removal, web off, turn cap, deny rules, then effort. Each token is
    a separate list element so callers can splice them without shell quoting.
    """
    args: list[str] = ["--disallowed-tools", _disallowed_tools(role)]
    # No direct web for any role (parity with the Claude path's tool set); the
    # roles that get web reach it through the gated roboco-search MCP server.
    args += ["--disable-web-search"]
    args += ["--max-turns", str(max_turns)]
    for rule in _deny_rules(role):
        args += ["--deny", rule]
    effort = _effort()
    if effort:
        args += ["--effort", effort]
    return args


def grok_cli_args(agent_id: str, *, max_turns: int = _DEFAULT_MAX_TURNS) -> list[str]:
    """The per-role grok flags for an agent, resolving its role from the id."""
    return grok_cli_args_for_role(get_agent_role(agent_id) or "", max_turns=max_turns)


def _load_mcp_config(path: str) -> dict[str, Any]:
    """Load the mounted mcp-config.json, tolerating a missing / invalid file."""
    try:
        with Path(path).open(encoding="utf-8") as fh:
            loaded = json.load(fh)
            return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_agents_md(
    *, source: Path = SYSTEM_PROMPT_PATH, dest: Path = GROK_AGENTS_PATH
) -> bool:
    """Install the mounted role blueprint as grok's global system prompt.

    Copies the composed prompt to ``~/.grok/AGENTS.md`` (the global instruction
    file grok honours in headless mode). Best-effort: returns False and writes
    nothing if the source is absent / unreadable, so a missing prompt never fails
    the render.
    """
    try:
        blueprint = source.read_text(encoding="utf-8")
    except OSError:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(blueprint, encoding="utf-8")
    return True


def bash_guard_hook_config(hook_path: str = BASH_GUARD_HOOK) -> dict[str, Any]:
    """The grok hooks JSON installing the bash-guard as a blocking PreToolUse hook.

    Matcher ``Bash`` covers grok's ``run_terminal_command`` alias too. The hook
    runs with ``ROBOCO_GUARD_SKIP_GIT=1`` so it only blocks the exfil categories
    (git ops stay on the graceful ``--deny`` rules); it denies via exit 2.
    """
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_path,
                            "env": {"ROBOCO_GUARD_SKIP_GIT": "1"},
                        }
                    ],
                }
            ]
        }
    }


def write_grok_hooks(
    *, hooks_dir: Path = GROK_HOOKS_DIR, hook_path: str = BASH_GUARD_HOOK
) -> bool:
    """Install the bash-guard PreToolUse hook into ``~/.grok/hooks/`` (best-effort).

    Skips writing (returns False) when the guard script is absent, so a missing
    hook never fails the render.
    """
    if not Path(hook_path).is_file():
        return False
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "roboco-bash-guard.json").write_text(
        json.dumps(bash_guard_hook_config(hook_path), indent=2), encoding="utf-8"
    )
    return True


def main() -> int:
    """Entrypoint: write ``~/.grok/config.toml`` + AGENTS.md + hooks + per-role args."""
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
    write_agents_md()
    write_grok_hooks()
    GROK_ARGS_PATH.write_text(
        "\n".join(grok_cli_args(agent_id, max_turns=max_turns)) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
