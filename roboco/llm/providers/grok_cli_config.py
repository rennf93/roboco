"""Render a Grok CLI agent's runtime config + per-role flags at container start.

Extended for full runtime hooks: in addition to config.toml / AGENTS.md / per-role
args, grok_cli_config now writes the complete set of hook JSONs (SessionStart for
sdk-startup, PreToolUse bash-guard, all PostToolUse, Stop, UserPromptSubmit,
PreCompact, SessionEnd) so AC2 is met by this module on container start.

The hooks use the exact same CLI-agnostic scripts as the Claude path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import tomli_w

from roboco.agents_config import get_agent_role
from roboco.services.gateway.role_config import get_role_config

# grok reads its global config from ``$HOME/.grok/config.toml`` ...
GROK_CONFIG_PATH = Path.home() / ".grok" / "config.toml"
GROK_AGENTS_PATH = Path.home() / ".grok" / "AGENTS.md"
SYSTEM_PROMPT_PATH = Path(
    os.environ.get("ROBOCO_SYSTEM_PROMPT", "/app/system-prompt.md")
)
GROK_HOOKS_DIR = Path.home() / ".grok" / "hooks"
BASH_GUARD_HOOK = os.environ.get(
    "ROBOCO_BASH_GUARD_HOOK", "/app/scripts/bash-guard-hook.sh"
)
GROK_ARGS_PATH = Path(os.environ.get("ROBOCO_GROK_ARGS_FILE", "/tmp/roboco-grok-args"))

# Also write user-settings.json for full hooks parity (grok discovers hooks here).
GROK_USER_SETTINGS_PATH = Path.home() / ".grok" / "user-settings.json"

_DEFAULT_MAX_TURNS = 200
_FULL_REASONING_OVERRIDES = frozenset({"default", "full", "none", ""})
_BASH_ROLES = frozenset({"developer", "documenter", "cell_pm", "main_pm"})
_SUBAGENT_ALLOWED_ROLES = frozenset({"prompter"})
_TOOL_SHELL = "run_terminal_cmd"
_TOOL_EDIT = "search_replace"
_TOOL_SUBAGENT = "Agent"

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
    "Bash(git tag -d*)",
    "Bash(git reflog delete*)",
)
_DESTRUCTIVE_DENY = ("Bash(rm -rf*)",)


def render_config_toml(mcp_config: dict[str, Any]) -> str:
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
    try:
        return bool(get_role_config(role).allows_write)
    except KeyError:
        return False


def _disallowed_tools(role: str) -> str:
    tools: list[str] = []
    if role not in _SUBAGENT_ALLOWED_ROLES:
        tools.append(_TOOL_SUBAGENT)
    if role not in _BASH_ROLES:
        tools.append(_TOOL_SHELL)
    if not _allows_write(role):
        tools.append(_TOOL_EDIT)
    return ",".join(tools)


def _deny_rules(role: str) -> list[str]:
    if role not in _BASH_ROLES:
        return []
    return [*_DESTRUCTIVE_DENY, *_GIT_MUTATE_DENY]


def _effort() -> str | None:
    override = os.environ.get("ROBOCO_GROK_REASONING_EFFORT", "").strip()
    if override and override.lower() not in _FULL_REASONING_OVERRIDES:
        return override.lower()
    return None


def grok_cli_args_for_role(
    role: str, *, max_turns: int = _DEFAULT_MAX_TURNS
) -> list[str]:
    args: list[str] = ["--always-approve"]
    args += ["--disallowed-tools", _disallowed_tools(role)]
    args += ["--disable-web-search"]
    args += ["--max-turns", str(max_turns)]
    for rule in _deny_rules(role):
        args += ["--deny", rule]
    effort = _effort()
    if effort:
        args += ["--effort", effort]
    return args


def grok_cli_args(agent_id: str, *, max_turns: int = _DEFAULT_MAX_TURNS) -> list[str]:
    return grok_cli_args_for_role(get_agent_role(agent_id) or "", max_turns=max_turns)


def _load_mcp_config(path: str) -> dict[str, Any]:
    try:
        with Path(path).open(encoding="utf-8") as fh:
            loaded = json.load(fh)
            return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_agents_md() -> bool:
    """Write AGENTS.md blueprint using current (possibly monkeypatched) globals."""
    source = SYSTEM_PROMPT_PATH
    dest = GROK_AGENTS_PATH
    try:
        blueprint = source.read_text(encoding="utf-8")
    except OSError:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(blueprint, encoding="utf-8")
    return True


def bash_guard_hook_config(hook_path: str = BASH_GUARD_HOOK) -> dict[str, Any]:
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


def _full_hooks_section() -> dict[str, Any]:
    """Return the complete hooks dict matching orchestrator / Claude parity.

    All scripts are the ones baked by agent-base; they are defensive (exit 0 on
    missing/no stdin or non-matching Grok payloads).
    """
    sdk = "/app/scripts/sdk-startup-hook.sh"
    a2a = "/app/scripts/a2a-check-hook.sh"
    budget = "/app/scripts/post-tool-budget-hook.sh"
    usage = "/app/scripts/usage-report-hook.sh"
    stop = "/app/scripts/stop-hook.sh"
    prompt = "/app/scripts/user-prompt-hook.sh"
    compact = "/app/scripts/pre-compact-hook.sh"
    end = "/app/scripts/session-end-hook.sh"
    guard = BASH_GUARD_HOOK

    return {
        "SessionStart": [{"hooks": [{"type": "command", "command": sdk}]}],
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": guard,
                        "env": {"ROBOCO_GUARD_SKIP_GIT": "1"},
                    }
                ],
            },
        ],
        "PostToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": a2a}]},
            {"matcher": "*", "hooks": [{"type": "command", "command": budget}]},
            {"matcher": "*", "hooks": [{"type": "command", "command": usage}]},
        ],
        "Stop": [
            {
                "hooks": [
                    {"type": "command", "command": stop},
                    {"type": "command", "command": usage},
                ]
            }
        ],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": prompt}]}],
        "PreCompact": [{"hooks": [{"type": "command", "command": compact}]}],
        "SessionEnd": [{"hooks": [{"type": "command", "command": end}]}],
    }


def write_grok_hooks(
    *, hooks_dir: Path = GROK_HOOKS_DIR, hook_path: str = BASH_GUARD_HOOK
) -> bool:
    """Install FULL set of runtime hook JSONs (for all event types).

    Writes one json per lifecycle event under ~/.grok/hooks/ (and also a
    consolidated user-settings.json) so grok_cli_config is the writer for AC2.
    Always includes bash-guard + all others.
    """
    if not Path(hook_path).is_file():
        # still proceed to write other hooks; guard optional
        pass
    hooks_dir.mkdir(parents=True, exist_ok=True)
    full = _full_hooks_section()

    # Write individual event json files (grok merges *.json from hooks dir)
    for event, entries in full.items():
        (hooks_dir / f"roboco-{event.lower()}.json").write_text(
            json.dumps({"hooks": {event: entries}}, indent=2), encoding="utf-8"
        )

    # Also write the bash-guard specific for backward compat with old name
    (hooks_dir / "roboco-bash-guard.json").write_text(
        json.dumps(bash_guard_hook_config(hook_path), indent=2), encoding="utf-8"
    )

    # Write full user-settings.json (the mechanism used by the orchestrator grok path)
    GROK_USER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    GROK_USER_SETTINGS_PATH.write_text(
        json.dumps({"hooks": full}, indent=2), encoding="utf-8"
    )
    return True


def main() -> int:
    """Entrypoint: write config.toml + AGENTS.md + FULL hooks + per-role args."""
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
