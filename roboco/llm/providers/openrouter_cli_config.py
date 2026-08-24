"""Render an opencode CLI agent's runtime config (``opencode.json``) at start.

The ``roboco-agent-openrouter`` image's entrypoint runs ``python -m
roboco.llm.providers.openrouter_cli_config`` to write the project-local
``opencode.json`` that opencode loads at startup. The config carries:

  * ``agent.roboco`` — the agent definition: ``prompt`` (the mounted role
    blueprint copied from ``system-prompt.md``), ``mode`` primary, ``model``
    (the OpenRouter model id from ``ROBOCO_AGENT_MODEL``), ``permission``
    (the per-role deny-rules — the bash-guard, see below), and per-tool
    toggles.
  * ``mcp`` — a near-passthrough of the mounted Claude Code
    ``mcp-config.json`` (opencode's ``mcp`` schema is Claude-identical,
    keyed by server name with ``{type, command, args, env}``).
  * ``provider.openrouter`` — the OpenRouter provider block (``api`` base
    URL, ``env`` the env-var names it reads, ``npm`` the AI SDK package).

Keeping the translation in importable Python (not a shell heredoc) makes it
unit-testable, mirroring :mod:`roboco.llm.providers.kimi_cli_config`.

Parity notes (where opencode's runtime model differs from kimi's):

  * **permission model** — opencode has NO PreToolUse hook system (the spike
    confirmed no hooks in the JS/schema; ``permission`` is the only
    pre-execution gate). The ``permission.bash`` deny-rules ARE the
    functional equivalent of RoboCo's bash-guard: they deny
    ``git push*`` / ``env`` / ``pip install*`` etc. BEFORE the command runs,
    gracefully (the agent gets a permission error and recovers, the run is
    never cancelled — the same semantics as kimi's deny-rules). The task's
    "+ bash-guard wrapper if opencode supports the hook" clause therefore
    SKIPS the wrapper: the permission model is the sole and sufficient
    boundary. opencode's config-file ``permission`` is a map of
    ``{permissionName: action | {pattern: action}}`` — ``permission.bash``
    maps a command-glob to ``"deny"`` / ``"ask"`` / ``"allow"``, and a
    per-tool toggle is a bare string action (``"edit": "allow"``,
    ``"task": "deny"``).
  * **system prompt** — the agent's ``prompt`` field (string) IS the system
    prompt; opencode loads it as the agent's standing instruction. The
    mounted role blueprint is copied verbatim into this field (the kimi
    analogue writes ``AGENTS.md``; opencode has no separate additive
    instruction file — the ``prompt`` field replaces it).
  * **auth preflight** — OpenRouter is the Ollama shape (static key, no
    refresh, no ``_auth.py``): ``--check`` just verifies the
    ``OPENROUTER_API_KEY`` env is non-empty. A missing key is a fail-fast
    exit 78 (the entrypoint's backstop), not a credential-file expiry read.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from roboco.agents_config import get_agent_role
from roboco.config import settings
from roboco.services.gateway.role_config import get_role_config

# opencode loads a project-local opencode.json from the working directory
# (the agent's workspace, set via docker run -w). Rendered fresh at start.
OPENCODE_CONFIG_PATH = Path(os.environ.get("ROBOCO_OPENCODE_CONFIG", "opencode.json"))

# The composed role blueprint the orchestrator mounts into every container.
SYSTEM_PROMPT_PATH = Path(
    os.environ.get("ROBOCO_SYSTEM_PROMPT", "/app/system-prompt.md")
)

# The opencode AI SDK npm package for OpenRouter (opencode resolves the
# provider transport from this; the env-var name in `env` is what opencode
# reads for the key).
_OPENROUTER_PROVIDER_NPM = "@ai-sdk/openrouter"
_OPENROUTER_PROVIDER_ENV = ["OPENROUTER_API_KEY"]

# --- Permission model (deny-only; --auto approves everything else) ----------
# opencode's config-file `permission` is a map: {name: action | {pattern:
# action}}. `bash` maps a command-glob to an action; a per-tool toggle is a
# bare string action. --auto auto-approves anything not explicitly denied,
# so scoping is entirely this deny set (parity with kimi's
# permission_rules_for_role, minus the Bash(...) wrapper opencode doesn't use).

# Fleet-wide, every role: subagent ban (opencode's `task` tool spawns
# subagents — CEO 2026-07-09 fleet-wide ban) + no direct web (gated web stays
# MCP-side) + no skill. opencode tool names: bash, read, edit, write, glob,
# grep, task, webfetch, todowrite, websearch, skill, lsp.
_FLEET_WIDE_TOOL_DENY: tuple[str, ...] = ("task", "webfetch", "websearch", "skill")

# Roles that legitimately run a shell (same set as kimi_cli_config._BASH_ROLES).
_BASH_ROLES = frozenset({"developer", "documenter", "cell_pm", "main_pm"})

# Git network/branch/history mutation, destructive shell, env/credential
# reads, and raw package-manager commands — the SAME canonical pattern set as
# kimi's _GIT_MUTATE_DENY / _DESTRUCTIVE_DENY / _RAW_PM_DENY, stripped of the
# Bash(...) wrapper opencode's permission.bash doesn't use. Denied gracefully
# (the agent gets a permission error and recovers; the run continues).
_GIT_MUTATE_DENY: dict[str, str] = {
    "git push*": "deny",
    "git fetch*": "deny",
    "git pull*": "deny",
    "git clone*": "deny",
    "git commit*": "deny",
    "git remote*": "deny",
    "git reset*": "deny",
    "git ls-remote*": "deny",
    "git checkout*": "deny",
    "git merge*": "deny",
    "git rebase*": "deny",
    "git cherry-pick*": "deny",
    "git revert*": "deny",
    "git update-ref*": "deny",
    "git tag -d*": "deny",
    "git reflog delete*": "deny",
}
_DESTRUCTIVE_DENY: dict[str, str] = {"rm -rf*": "deny"}
_ENV_CRED_DENY: dict[str, str] = {
    "env": "deny",
    "printenv": "deny",
    "printenv*": "deny",
}
_RAW_PM_DENY: dict[str, str] = {
    "uv run*": "deny",
    "uv sync*": "deny",
    "uv pip install*": "deny",
    "uv pip uninstall*": "deny",
    "uv lock*": "deny",
    "uv add*": "deny",
    "uv remove*": "deny",
    "pip install*": "deny",
    "pip3 install*": "deny",
    "pip uninstall*": "deny",
    "conda install*": "deny",
    "conda create*": "deny",
    "conda run*": "deny",
    "poetry run*": "deny",
    "poetry install*": "deny",
    "poetry add*": "deny",
}


def _allows_write(role: str) -> bool:
    """True if the role writes code (``role_config.allows_write``)."""
    try:
        return bool(get_role_config(role).allows_write)
    except KeyError:
        return False


def permission_for_role(role: str) -> dict[str, Any]:
    """The ``permission`` map gating one role (opencode config-file shape).

    Fleet-wide tool denies apply to every role; a non-bash-capable role gets a
    blanket ``bash: "deny"`` (closing the Write-via-bash bypass); a
    bash-capable role keeps the shell but gets the git-mutation/destructive/
    env-cred/raw-PM command-glob denies under ``bash``. Non-author roles
    (``role_config.allows_write`` is False) additionally get
    ``write``/``edit`` denied.
    """
    perm: dict[str, Any] = dict.fromkeys(_FLEET_WIDE_TOOL_DENY, "deny")
    if not _allows_write(role):
        perm["write"] = "deny"
        perm["edit"] = "deny"
    if role not in _BASH_ROLES:
        perm["bash"] = "deny"
        return perm
    bash_rules: dict[str, str] = {}
    bash_rules.update(_DESTRUCTIVE_DENY)
    bash_rules.update(_GIT_MUTATE_DENY)
    bash_rules.update(_ENV_CRED_DENY)
    bash_rules.update(_RAW_PM_DENY)
    perm["bash"] = bash_rules
    return perm


def _tools_for_role(role: str) -> dict[str, bool]:
    """The ``tools`` toggle map (deprecated in opencode but kept for clarity).

    bash-capable roles keep bash; non-bash roles disable it. Read/glob/grep
    are always on; write/edit track allows_write; fleet-wide-denied tools are
    off.
    """
    tools: dict[str, bool] = {
        "bash": role in _BASH_ROLES,
        "read": True,
        "glob": True,
        "grep": True,
        "todowrite": True,
        "lsp": True,
        "write": _allows_write(role),
        "edit": _allows_write(role),
    }
    for name in _FLEET_WIDE_TOOL_DENY:
        tools[name] = False
    return tools


def _load_system_prompt(path: Path = SYSTEM_PROMPT_PATH) -> str:
    """Load the mounted role blueprint; empty string if absent/unreadable.

    Best-effort: a missing prompt never fails the render (opencode falls back
    to its built-in build agent description), but a real fleet spawn always
    mounts one.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def render_agent_block(role: str, model: str) -> dict[str, Any]:
    """The ``agent.roboco`` block: prompt + mode + model + permission + tools."""
    return {
        "prompt": _load_system_prompt(),
        "description": "RoboCo agent — follows the mounted role blueprint.",
        "mode": "primary",
        "model": model,
        "permission": permission_for_role(role),
        "tools": _tools_for_role(role),
    }


def render_provider_block(base_url: str) -> dict[str, dict[str, Any]]:
    """The ``provider.openrouter`` block opencode resolves the transport from.

    ``api`` is the OpenRouter endpoint; ``env`` names the env var opencode
    reads for the key (OPENROUTER_API_KEY, injected at spawn); ``npm`` is the
    AI SDK package. ``name`` is the display label.
    """
    return {
        "openrouter": {
            "api": base_url,
            "name": "OpenRouter",
            "env": list(_OPENROUTER_PROVIDER_ENV),
            "npm": _OPENROUTER_PROVIDER_NPM,
        }
    }


def _load_mcp_config(path: str) -> dict[str, Any]:
    """Load the mounted mcp-config.json, tolerating a missing / invalid file."""
    try:
        with Path(path).open(encoding="utf-8") as fh:
            loaded = json.load(fh)
            return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def render_mcp_block(mcp_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Render the ``mcp`` block — a near-passthrough of the mounted mcp-config.

    opencode's ``mcp`` schema is Claude-identical (``{command, args, env}``
    keyed by server name, under ``mcpServers`` in the source). opencode uses
    ``type: "local"`` for stdio servers.
    """
    servers: dict[str, dict[str, Any]] = {}
    for name, spec in (mcp_config.get("mcpServers") or {}).items():
        block: dict[str, Any] = {
            "type": "local",
            "command": [str(spec.get("command", ""))]
            + [str(a) for a in (spec.get("args") or [])],
        }
        env = spec.get("env") or {}
        if env:
            block["env"] = {str(k): str(v) for k, v in env.items()}
        servers[str(name)] = block
    return servers


def render_config(
    role: str,
    model: str,
    base_url: str,
    mcp_config_path: str,
) -> dict[str, Any]:
    """Render the full ``opencode.json`` config as a dict."""
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "agent": {"roboco": render_agent_block(role, model)},
        "provider": render_provider_block(base_url),
    }
    mcp = render_mcp_block(_load_mcp_config(mcp_config_path))
    if mcp:
        config["mcp"] = mcp
    return config


def is_valid() -> bool:
    """Auth preflight: True when ``OPENROUTER_API_KEY`` is non-empty.

    The Ollama shape — a static key, no refresh, no ``_auth.py``. This is
    purely the entrypoint's fail-fast backstop (a missing key exits 78 before
    the CLI hangs or fails deep into the run); there is no expiry to read.
    """
    return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())


def main(argv: list[str] | None = None) -> int:
    """Entrypoint: ``--check`` runs the auth preflight; else renders opencode.json."""
    args = argv if argv is not None else sys.argv[1:]
    if "--check" in args:
        return 0 if is_valid() else 1

    agent_id = os.environ.get("ROBOCO_AGENT_ID", "")
    mcp_path = os.environ.get("ROBOCO_MCP_CONFIG", "/app/mcp-config.json")
    role = get_agent_role(agent_id) or ""
    model = os.environ.get("ROBOCO_AGENT_MODEL", settings.openrouter_cli_model)
    base_url = os.environ.get("OPENROUTER_BASE_URL", settings.openrouter_base_url)

    config = render_config(role, model, base_url, mcp_path)
    OPENCODE_CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
