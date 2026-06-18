"""Generate an ``opencode.json`` for a Grok (xAI) agent at container start.

The ``roboco-agent-grok`` image's entrypoint runs ``python -m
roboco.llm.providers.opencode_config`` to turn the env contract ``GrokProvider``
sets (``OPENAI_*`` + ``ROBOCO_*``) plus the mounted Claude Code
``mcp-config.json`` into the ``opencode.json`` that opencode reads. Keeping this
as importable Python (not a shell heredoc) makes the translation unit-testable.

Config shape per opencode docs (https://opencode.ai/docs/config):
  * NO ``provider`` block — opencode's BUILT-IN xai provider already drives
    grok-build-0.1 (model resolution + tool-calls verified live), so a custom
    block is unnecessary. The key + base URL reach the provider via the
    ``XAI_API_KEY`` / ``XAI_BASE_URL`` env vars; ``model`` selects ``xai/<model>``.
  * ``mcp.<name>`` — ``{type:"local", command:[...], environment:{...}}``; this
    is where RoboCo's gateway servers (roboco-flow / roboco-do / ...) are wired,
    translated from Claude Code's ``mcpServers`` (``command`` + ``args`` + ``env``).
  * ``permission.{bash,edit,external_directory}`` and ``instructions`` (system
    prompt + briefing).
  * ``tools`` — opencode's subagent ``task`` tool is hard-disabled. No RoboCo role
    uses opencode-internal subagents (work is driven through the gateway verbs),
    and a ``task``-spawned subagent on ``grok-build-0.1`` whose model call opens an
    idle stream hangs the parent run with no recovery (observed live on a PR
    review). This is the primary idle-stream defence (the orchestrator reaper is
    the backstop).

There is NO ``plugin`` key: the plugins are baked into opencode's plugin
AUTO-DISCOVERY dir (``~/.config/opencode/plugin/``, i.e.
``/home/agent/.config/opencode/plugin/`` in the image), so the generated config
doesn't need to reference them by path (secret-scrub + budget-feed in the base
grok image; the Secretary's directive tools and the Intake's propose_draft in
their interactive images). Each uses a NAMED export (opencode's documented
plugin convention). The model writes this config to the GLOBAL location (see
``main`` — ``~/.config/opencode/opencode.json``), which is what opencode reads.

GUARDRAIL PARITY: the bash-guard (PAT-scrub) is ported via ``secret-scrub.js``
(``tool.execute.before``); the per-session budget / loop / terminal-verb
counters and the per-verb circuit breaker are restored by starting the same
in-container SDK server the Claude path runs (the grok entrypoint launches
``roboco.agent_sdk.server``) and feeding it from ``budget-feed.js``
(``tool.execute.{before,after}``); usage/cost is captured from opencode's SQLite
store at finalize and bounded by the orchestrator cost watchdog
(``ROBOCO_GROK_MAX_COST_USD``); the prompt-injection guard is recreated at
RoboCo's input boundary (``roboco.agent_sdk.prompt_guard``); and the SessionEnd
post-mortem + Stop silent-exit substitute run at the entrypoint boundary after
``opencode run`` returns. ``bash`` / ``edit`` permissions are scoped per role
(read-only roles get ``edit=deny``; only delivery roles get ``bash``) and stay
operator-tunable (``ROBOCO_GROK_BASH_PERMISSION`` / ``ROBOCO_GROK_EDIT_PERMISSION``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_OPENCODE_SCHEMA = "https://opencode.ai/config.json"
_PROVIDER_ID = "xai"
# No provider block at all: opencode's BUILT-IN xai provider already drives
# grok-build-0.1 with working tool-calls (verified live), so no custom `npm` /
# `models` override is needed. The xAI key is injected via the XAI_API_KEY env
# var the built-in provider reads (set by GrokProvider / the orchestrator).
#
# Plugins (secret-scrub / budget-feed / the per-role tool plugins) are baked into
# the plugin AUTO-DISCOVERY dir (~/.config/opencode/plugin/, i.e.
# /home/agent/.config/opencode/plugin/ in the image) rather than referenced by a
# config `plugin:` path — the dir is the simplest registration route and keeps
# the generated config path-free (registration verified live against grok-build-0.1).


# opencode's built-in subagent-spawning tool. Hard-disabled in the generated
# config (see the module docstring): a RoboCo agent never spawns opencode's own
# subagents, and one that does can wedge the parent run on an idle stream. This
# is the primary defence against the idle-stream hang; the orchestrator's
# reaper watchdog (_maybe_kill_wedged_grok) is the backstop. (Per-provider
# request/stream timeouts would need a custom provider block, which we don't
# emit; the reaper + disabled subagents cover the idle-stream risk instead.)
_SUBAGENT_TOOL = "task"


@dataclass(frozen=True)
class OpencodeGuards:
    """Tunable runtime guards baked into a Grok ``opencode.json``.

    ``bash``/``edit`` gate the command/file tools; ``external_directory`` gates
    reading paths outside the project cwd (opencode auto-DENIES an ``ask`` in
    headless mode, which blocked the pr-reviewer from reading a diff it wrote to
    /tmp — so default ``allow``: the container is the sandbox and secret-scrub
    still blocks credential files); ``disable_subagents`` removes the subagent
    ``task`` tool.
    """

    bash_permission: str = "allow"
    edit_permission: str = "allow"
    external_directory_permission: str = "allow"
    disable_subagents: bool = True


def translate_mcp_servers(mcp_config: dict[str, Any]) -> dict[str, Any]:
    """Translate Claude Code ``mcpServers`` into opencode's ``mcp`` block.

    ``{"command": "uv", "args": [...], "env": {...}}`` becomes
    ``{"type": "local", "command": ["uv", ...], "environment": {...},
    "enabled": True}``.
    """
    servers = mcp_config.get("mcpServers", {})
    out: dict[str, Any] = {}
    for name, spec in servers.items():
        command = spec.get("command")
        args = list(spec.get("args", []))
        cmd_list = [command, *args] if command else args
        entry: dict[str, Any] = {
            "type": "local",
            "command": cmd_list,
            "enabled": True,
        }
        env = spec.get("env")
        if env:
            entry["environment"] = env
        out[name] = entry
    return out


def build_opencode_config(
    mcp_config: dict[str, Any],
    model: str,
    *,
    instruction_paths: list[str],
    guards: OpencodeGuards | None = None,
) -> dict[str, Any]:
    """Build the ``opencode.json`` dict for a Grok agent.

    Emits NO ``provider`` block — opencode's BUILT-IN xai provider drives
    grok-build-0.1 (verified live), so a custom block is unnecessary; the key +
    base URL are injected via the ``XAI_API_KEY`` / ``XAI_BASE_URL`` env vars
    (set by GrokProvider / the orchestrator). No ``plugin`` array either —
    plugins live in the auto-discovery dir baked into the images.
    """
    guards = guards or OpencodeGuards()
    config: dict[str, Any] = {
        "$schema": _OPENCODE_SCHEMA,
        "model": f"{_PROVIDER_ID}/{model}",
        "mcp": translate_mcp_servers(mcp_config),
        "permission": {
            "bash": guards.bash_permission,
            "edit": guards.edit_permission,
            # Reading paths outside the project cwd (e.g. /tmp scratch). opencode
            # auto-denies an "ask" in headless mode, which blocked the pr-reviewer
            # from reading a diff it wrote to /tmp; "allow" since the container is
            # the sandbox and secret-scrub still blocks credential files.
            "external_directory": guards.external_directory_permission,
        },
        "instructions": instruction_paths,
    }
    if guards.disable_subagents:
        # Remove the subagent tool entirely so the model can never invoke it.
        config["tools"] = {_SUBAGENT_TOOL: False}
    return config


def _load_mcp_config(path: str) -> dict[str, Any]:
    """Load the mounted mcp-config.json, tolerating a missing/invalid file."""
    try:
        with Path(path).open() as fh:
            data: dict[str, Any] = json.load(fh)
            return data
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> int:
    """Entrypoint: read env + mounted mcp-config.json, write opencode.json.

    The xAI key + base URL are NOT read here — they reach opencode's built-in
    xai provider via the ``XAI_API_KEY`` / ``XAI_BASE_URL`` env vars.
    """
    model = os.environ.get("ROBOCO_AGENT_MODEL", "grok-build-0.1")
    mcp_path = os.environ.get("ROBOCO_MCP_CONFIG", "/app/mcp-config.json")
    system_prompt = os.environ.get("ROBOCO_SYSTEM_PROMPT", "/app/system-prompt.md")
    # Default to opencode's global config location so it is found regardless of
    # the agent's working directory (cwd is the per-agent workspace at spawn).
    out_path = os.environ.get(
        "ROBOCO_OPENCODE_CONFIG",
        str(Path.home() / ".config" / "opencode" / "opencode.json"),
    )
    guards = OpencodeGuards(
        bash_permission=os.environ.get("ROBOCO_GROK_BASH_PERMISSION", "allow"),
        edit_permission=os.environ.get("ROBOCO_GROK_EDIT_PERMISSION", "allow"),
        external_directory_permission=os.environ.get(
            "ROBOCO_GROK_EXTERNAL_DIR_PERMISSION", "allow"
        ),
    )

    # Instructions = system prompt + the SessionStart briefing when mounted.
    candidates = [system_prompt, "/app/briefing.md"]
    instructions = [p for p in candidates if p and Path(p).exists()]

    config = build_opencode_config(
        _load_mcp_config(mcp_path),
        model,
        instruction_paths=instructions,
        guards=guards,
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        json.dump(config, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
