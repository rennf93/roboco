"""Render an opencode CLI agent's runtime config at start.

The ``roboco-agent-nebius`` image's entrypoint runs ``python -m
roboco.llm.providers.nebius_cli_config`` to write opencode's GLOBAL
config - ``~/.config/opencode/opencode.json`` plus the bash-guard plugin
under ``~/.config/opencode/plugins/``. The global location is
cwd-independent BY CONSTRUCTION: the render step executes from ``/app`` (so
``python -m`` resolves the installed roboco package, not a workspace clone's
shadowing copy), while ``opencode run`` itself executes at the container's
real ``-w`` cwd - the agent's workspace for developer/documenter roles. A
project-local ``opencode.json`` rendered into /app would simply never be
found at run time. The config carries:

  * ``agent.roboco`` - the agent definition: ``prompt`` (the mounted role
    blueprint copied from ``system-prompt.md``), ``mode`` primary, ``model``
    (the Nebius model id from ``ROBOCO_AGENT_MODEL`` resolved through
    :func:`opencode_model_ref` - see below), ``permission`` (the per-role
    deny-rules - the bash-guard, see below), and per-tool toggles.
  * ``mcp`` - a near-passthrough of the mounted Claude Code
    ``mcp-config.json`` (opencode's ``mcp`` schema is Claude-identical,
    keyed by server name with ``{type, command, args, env}``).
  * ``provider.nebius`` - the Nebius provider block (``api`` base
    URL, ``env`` the env-var names it reads, ``npm`` the AI SDK package).

Keeping the translation in importable Python (not a shell heredoc) makes it
unit-testable, mirroring :mod:`roboco.llm.providers.openrouter_cli_config`
(this module is its Nebius twin - the permission model, bash-guard plugin,
and config shape are byte-identical by design; only the provider block, the
model-ref prefix, and the auth env differ).

Nebius-specific notes (where Token Factory differs from OpenRouter):

  * **provider transport** - Token Factory is a plain OpenAI-compatible
    endpoint (no provider-specific AI SDK), so the block uses opencode's
    generic ``@ai-sdk/openai-compatible`` package: ``api`` is the baseURL,
    ``env`` names the key var opencode reads (NEBIUS_API_KEY, injected at
    spawn). Model ids carry a vendor prefix on Token Factory
    (``nvidia/nemotron-3-super-120b``), so the opencode model ref is
    ``nebius/<vendor>/<model>`` - opencode splits the ref at the FIRST
    slash (its provider id) and passes the rest to the provider block.
  * **auth preflight** - Nebius is the Ollama shape (static key, no
    refresh, no ``_auth.py``): ``--check`` just verifies the
    ``NEBIUS_API_KEY`` env is non-empty. A missing key is a fail-fast
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

# opencode's native GLOBAL config home (cwd-independent): the render step
# runs from /app, but `opencode run` executes at the container's real -w cwd
# (the agent's workspace for developer/documenter roles), so a project-local
# opencode.json is never found there. The global location loads regardless
# of cwd. Rendered fresh at start; ROBOCO_OPENCODE_CONFIG overrides (tests).
OPENCODE_CONFIG_PATH = Path(
    os.environ.get("ROBOCO_OPENCODE_CONFIG", "~/.config/opencode/opencode.json")
).expanduser()

# The rendered bash-guard plugin (see render_bash_guard_plugin) - opencode
# loads every ``*.js`` in its global plugins dir alongside the global config.
OPENCODE_PLUGIN_PATH = Path(
    os.environ.get(
        "ROBOCO_OPENCODE_PLUGIN",
        "~/.config/opencode/plugins/roboco-bash-guard.js",
    )
).expanduser()

# The bash-guard PreToolUse hook script, baked into the agent base image -
# the SAME script the Claude/grok/kimi paths install (accepts the Claude
# snake_case stdin payload unmodified; exit 0 = allow, exit 2 = deny).
BASH_GUARD_HOOK = os.environ.get(
    "ROBOCO_BASH_GUARD_HOOK", "/app/scripts/bash-guard-hook.sh"
)

# The composed role blueprint the orchestrator mounts into every container.
SYSTEM_PROMPT_PATH = Path(
    os.environ.get("ROBOCO_SYSTEM_PROMPT", "/app/system-prompt.md")
)

# The opencode AI SDK npm package for Nebius (opencode resolves the provider
# transport from this; the env-var name in `env` is what opencode reads for
# the key). Token Factory speaks the generic OpenAI-compatible protocol, so
# the transport is the generic AI SDK package, not a vendor-specific one.
_NEBIUS_PROVIDER_NPM = "@ai-sdk/openai-compatible"
_NEBIUS_PROVIDER_ENV = ["NEBIUS_API_KEY"]

# --- Permission model (deny-only; --auto approves everything else) ----------
# opencode's config-file `permission` is a map: {name: action | {pattern:
# action}}. `bash` maps a command-glob to an action; a per-tool toggle is a
# bare string action. --auto auto-approves anything not explicitly denied,
# so scoping is entirely this deny set (parity with kimi's
# permission_rules_for_role, minus the Bash(...) wrapper opencode doesn't use).

# Fleet-wide, every role: subagent ban (opencode's `task` tool spawns
# subagents - CEO 2026-07-09 fleet-wide ban) + no direct web (gated web stays
# MCP-side) + no skill. opencode tool names: bash, read, edit, write, glob,
# grep, task, webfetch, todowrite, websearch, skill, lsp.
_FLEET_WIDE_TOOL_DENY: tuple[str, ...] = ("task", "webfetch", "websearch", "skill")

# Roles that legitimately run a shell (same set as kimi_cli_config._BASH_ROLES).
_BASH_ROLES = frozenset({"developer", "documenter", "cell_pm", "main_pm"})

# Git network/branch/history mutation, destructive shell, env/credential
# reads, and raw package-manager commands - the SAME canonical pattern set as
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


def _load_system_prompt(path: Path) -> str:
    """Load the mounted role blueprint; empty string if absent/unreadable.

    Best-effort: a missing prompt never fails the render (opencode falls back
    to its built-in build agent description), but a real fleet spawn always
    mounts one.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def opencode_model_ref(model: str) -> str:
    """Resolve *model* to opencode's ``provider/model`` grammar.

    The orchestrator passes a BARE Nebius catalog id (``nvidia/nemotron-3-
    super-120b``) in ``ROBOCO_AGENT_MODEL`` - passed through verbatim,
    opencode resolves it against its BUILT-IN ``anthropic`` provider (which
    expects ``ANTHROPIC_API_KEY``, never set in this image) and the run
    cannot authenticate (live-verified on the openrouter twin). Prefixing
    with opencode's own provider id from the rendered ``provider.nebius``
    block routes every call through Nebius:
    ``nebius/nvidia/nemotron-3-super-120b``. Idempotent - an
    already-prefixed ref passes through untouched (the provider injects the
    prefixed ref into ``ROBOCO_AGENT_MODEL`` and the renderer re-derives it).
    """
    stripped = model.strip()
    if not stripped or stripped.startswith("nebius/"):
        return stripped
    return f"nebius/{stripped}"


def render_agent_block(role: str, model: str) -> dict[str, Any]:
    """The ``agent.roboco`` block: prompt + mode + model + permission + tools.

    ``model`` is resolved through :func:`opencode_model_ref` - the block must
    name the model the same way the entrypoint's ``--model`` flag does, or
    the agent block and the CLI flag route to different providers.
    """
    return {
        "prompt": _load_system_prompt(SYSTEM_PROMPT_PATH),
        "description": "RoboCo agent - follows the mounted role blueprint.",
        "mode": "primary",
        "model": opencode_model_ref(model),
        "permission": permission_for_role(role),
        "tools": _tools_for_role(role),
    }


def render_provider_block(base_url: str) -> dict[str, dict[str, Any]]:
    """The ``provider.nebius`` block opencode resolves the transport from.

    ``api`` is the Token Factory endpoint; ``env`` names the env var opencode
    reads for the key (NEBIUS_API_KEY, injected at spawn); ``npm`` is the
    generic OpenAI-compatible AI SDK package (Token Factory needs no
    vendor-specific transport, unlike OpenRouter's). ``name`` is the display
    label.
    """
    return {
        "nebius": {
            "api": base_url,
            "name": "Nebius",
            "env": list(_NEBIUS_PROVIDER_ENV),
            "npm": _NEBIUS_PROVIDER_NPM,
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
    """Render the ``mcp`` block - a near-passthrough of the mounted mcp-config.

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


# The opencode bash-guard plugin source (rendered verbatim into the global
# plugins dir; ``__BASH_GUARD_HOOK__`` is replaced with BASH_GUARD_HOOK at
# render time). opencode plugins are JS modules exporting an async factory
# that returns a map of hook handlers (Bun runtime - node: built-ins work).
# ``tool.execute.before`` is a genuine PreToolUse-equivalent (live-verified -
# it fires before the tool spawns, and throwing blocks the call gracefully:
# the agent sees the message and recovers, the run is never cancelled). The
# plugin feeds every bash call through the SAME bash-guard-hook.sh the
# Claude/grok/kimi paths install, using the hook's Claude-schema stdin
# payload ({tool_name, tool_input:{command}}; exit 0 = allow, exit 2 = deny)
# - defense-in-depth against the compound-command exfil vectors the
# first-token permission.bash globs cannot reach (the deny-rules above stay
# the PRIMARY gate). Fail-open on hook-infrastructure errors (script missing
# or not executable): a broken tripwire must not take down every run while
# the deny-rules are still in force; only an authoritative exit 2 blocks.
_BASH_GUARD_PLUGIN_TEMPLATE = """\
// RoboCo bash-guard plugin for opencode (rendered by
// roboco.llm.providers.nebius_cli_config - do not edit by hand; the
// entrypoint re-renders it at every start).
//
// Defense-in-depth PreToolUse gate: opencode's `tool.execute.before` hook
// fires BEFORE the bash tool spawns the command, and throwing inside the
// handler blocks the call (the agent sees the message and recovers - the
// same graceful semantics as opencode's permission.bash deny-rules, which
// remain the primary gate). Every bash call is fed through the SAME
// bash-guard-hook.sh the Claude/grok/kimi paths install, using the hook's
// Claude-schema stdin payload: {tool_name, tool_input:{command}};
// exit 0 = allow, exit 2 = deny. The hook catches compound-command
// exfil/mutation vectors (`cd /ws && git fetch ...`) that permission.bash's
// first-token globs cannot reach.
//
// Fail-open on hook-infrastructure errors (missing/unexecutable script): a
// broken tripwire must not break every run while the deny-rules hold. Only
// the hook's authoritative deny exit code (2) blocks.

import { spawnSync } from "node:child_process"

const HOOK_PATH = "__BASH_GUARD_HOOK__"

export const robocoBashGuard = async () => {
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "bash") return
      const command = output.args && output.args.command
      if (typeof command !== "string" || command === "") return
      let result
      try {
        result = spawnSync(HOOK_PATH, {
          input: JSON.stringify({
            tool_name: "bash",
            tool_input: { command },
          }),
          encoding: "utf8",
        })
      } catch {
        return // fail open on infrastructure errors
      }
      if (result.error) return // script missing / not executable
      if (result.status === 2) {
        throw new Error(
          "Blocked by the RoboCo bash-guard: this command matches a denied " +
            "pattern. Route git and package operations through the RoboCo " +
            "MCP gateway verbs instead.",
        )
      }
    },
  }
}
"""


def render_bash_guard_plugin(hook_path: str | None = None) -> str:
    """The opencode bash-guard plugin JS source (``tool.execute.before`` hook).

    *hook_path* defaults to :data:`BASH_GUARD_HOOK` read at call time (the
    round-1 stale-default-arg lesson). See ``_BASH_GUARD_PLUGIN_TEMPLATE``
    for the gate's contract.
    """
    return _BASH_GUARD_PLUGIN_TEMPLATE.replace(
        "__BASH_GUARD_HOOK__", hook_path or BASH_GUARD_HOOK
    )


def write_bash_guard_plugin(path: Path | None = None) -> Path:
    """Render the bash-guard plugin to opencode's global plugins dir.

    Creates parent dirs; OSError propagates so a render failure is loud at
    container start (before any model call), not silently unguarded.
    """
    target = path if path is not None else OPENCODE_PLUGIN_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_bash_guard_plugin(), encoding="utf-8")
    return target


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
    """Auth preflight: True when ``NEBIUS_API_KEY`` is non-empty.

    The Ollama shape - a static key, no refresh, no ``_auth.py``. This is
    purely the entrypoint's fail-fast backstop (a missing key exits 78 before
    the CLI hangs or fails deep into the run); there is no expiry to read.
    """
    return bool(os.environ.get("NEBIUS_API_KEY", "").strip())


def main(argv: list[str] | None = None) -> int:
    """Entrypoint: ``--check`` runs the auth preflight; else renders the config.

    Render mode writes BOTH artifacts opencode loads from its global config
    home: ``opencode.json`` (agent/permission/mcp/provider blocks) and the
    bash-guard plugin (see :func:`write_bash_guard_plugin`).
    """
    args = argv if argv is not None else sys.argv[1:]
    if "--check" in args:
        return 0 if is_valid() else 1

    agent_id = os.environ.get("ROBOCO_AGENT_ID", "")
    mcp_path = os.environ.get("ROBOCO_MCP_CONFIG", "/app/mcp-config.json")
    role = get_agent_role(agent_id) or ""
    model = os.environ.get("ROBOCO_AGENT_MODEL", settings.nebius_cli_model)
    base_url = os.environ.get("NEBIUS_BASE_URL", settings.nebius_base_url)

    config = render_config(role, model, base_url, mcp_path)
    OPENCODE_CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    write_bash_guard_plugin()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
