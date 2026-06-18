"""Generate an ``opencode.json`` for a Grok (xAI) agent at container start.

The ``roboco-agent-grok`` image's entrypoint runs ``python -m
roboco.llm.providers.opencode_config`` to turn the env contract ``GrokProvider``
sets (``OPENAI_*`` + ``ROBOCO_*``) plus the mounted Claude Code
``mcp-config.json`` into the ``opencode.json`` that opencode reads. Keeping this
as importable Python (not a shell heredoc) makes the translation unit-testable.

Config shape per opencode docs (https://opencode.ai/docs/config):
  * ``provider.<id>`` — ``@ai-sdk/openai`` (the Responses API; see ``_PROVIDER_NPM``)
    with ``options.baseURL`` / ``options.apiKey`` / ``options.timeout`` /
    ``options.chunkTimeout``; ``model`` selects ``<id>/<model>``.
  * ``mcp.<name>`` — ``{type:"local", command:[...], environment:{...}}``; this
    is where RoboCo's gateway servers (roboco-flow / roboco-do / ...) are wired,
    translated from Claude Code's ``mcpServers`` (``command`` + ``args`` + ``env``).
  * ``permission.{bash,edit}`` and ``instructions`` (system prompt + briefing).
  * ``tools`` — opencode's subagent ``task`` tool is hard-disabled. No RoboCo role
    uses opencode-internal subagents (work is driven through the gateway verbs),
    and a ``task``-spawned subagent on ``grok-build-0.1`` whose model call opens an
    idle stream hangs the parent run with no recovery (observed live on a PR
    review). The request/stream timeouts below are the defence-in-depth backstop.

GUARDRAIL PARITY: the bash-guard (PAT-scrub) is ported via ``secret-scrub.js``
(``tool.execute.before``); usage/cost is captured from opencode's SQLite store at
finalize and bounded by the orchestrator cost watchdog
(``ROBOCO_GROK_MAX_COST_USD``, which also catches runaway-loop burn); and the
prompt-injection guard is recreated at RoboCo's input boundary
(``roboco.agent_sdk.prompt_guard`` — the driver scans interactive turns, the
entrypoint scans the one-shot task prompt). The only Claude hook without an
opencode equivalent is the stop-guard (terminal-verb enforcement; opencode's
stop events are observe-only) — a workflow nicety, not a security control.
``bash`` permission stays operator-tunable (``ROBOCO_GROK_BASH_PERMISSION``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_OPENCODE_SCHEMA = "https://opencode.ai/config.json"
_PROVIDER_ID = "xai"
# grok-build-0.1 is driven through the OpenAI **Responses** API (opencode calls
# model.responses()). Only @ai-sdk/openai implements that — @ai-sdk/openai-compatible
# is chat/completions only and errors with "responses is not a function".
# Confirmed via a live opencode run against api.x.ai/v1.
_PROVIDER_NPM = "@ai-sdk/openai"

# Plugins baked into the roboco-agent-grok image (see docker/agent-grok.Dockerfile).
# secret-scrub ports the bash-guard deny rules to opencode's tool.execute.before.
_PLUGINS = ["/app/opencode-plugins/secret-scrub.js"]

# opencode's built-in subagent-spawning tool. Hard-disabled in the generated
# config (see the module docstring): a RoboCo agent never spawns opencode's own
# subagents, and one that does can wedge the parent run on an idle stream.
_SUBAGENT_TOOL = "task"

# Request / stream timeouts (ms) written into ``provider.xai.options``. ``timeout``
# bounds a single model call; ``chunkTimeout`` aborts a stream that goes idle for
# this long (no chunk arrives) — the backstop for the idle-SSE hang. Both are
# operator-tunable via env (see ``main``).
_DEFAULT_REQUEST_TIMEOUT_MS = 300_000
_DEFAULT_CHUNK_TIMEOUT_MS = 120_000


def _env_int(name: str, default: int) -> int:
    """Read a positive int from env ``name``; fall back to ``default``.

    A missing, blank, non-integer, or non-positive value yields ``default`` so a
    typo in an operator override can never disable the timeout entirely.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class XaiTarget:
    """The xAI endpoint a Grok agent talks to."""

    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class OpencodeGuards:
    """Tunable runtime guards baked into a Grok ``opencode.json``.

    ``bash``/``edit`` gate the command/file tools; ``external_directory`` gates
    reading paths outside the project cwd (opencode auto-DENIES an ``ask`` in
    headless mode, which blocked the pr-reviewer from reading a diff it wrote to
    /tmp — so default ``allow``: the container is the sandbox and secret-scrub
    still blocks credential files); the timeouts bound a single model call and
    abort an idle stream; ``disable_subagents`` removes the subagent ``task`` tool.
    """

    bash_permission: str = "allow"
    edit_permission: str = "allow"
    external_directory_permission: str = "allow"
    request_timeout_ms: int = _DEFAULT_REQUEST_TIMEOUT_MS
    chunk_timeout_ms: int = _DEFAULT_CHUNK_TIMEOUT_MS
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
    target: XaiTarget,
    *,
    instruction_paths: list[str],
    guards: OpencodeGuards | None = None,
) -> dict[str, Any]:
    """Build the full ``opencode.json`` dict for a Grok agent."""
    guards = guards or OpencodeGuards()
    config: dict[str, Any] = {
        "$schema": _OPENCODE_SCHEMA,
        "provider": {
            _PROVIDER_ID: {
                "npm": _PROVIDER_NPM,
                "name": "xAI",
                "options": {
                    "baseURL": target.base_url,
                    "apiKey": target.api_key,
                    "timeout": guards.request_timeout_ms,
                    "chunkTimeout": guards.chunk_timeout_ms,
                },
                "models": {target.model: {"name": target.model}},
            }
        },
        "model": f"{_PROVIDER_ID}/{target.model}",
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
        # Command guard / secret-scrub (bash-guard parity). Baked into the image.
        "plugin": list(_PLUGINS),
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
    """Entrypoint: read env + mounted mcp-config.json, write opencode.json."""
    target = XaiTarget(
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.x.ai/v1"),
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        model=os.environ.get("ROBOCO_AGENT_MODEL", "grok-build-0.1"),
    )
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
        external_directory_permission=os.environ.get(
            "ROBOCO_GROK_EXTERNAL_DIR_PERMISSION", "allow"
        ),
        request_timeout_ms=_env_int(
            "ROBOCO_GROK_REQUEST_TIMEOUT_MS", _DEFAULT_REQUEST_TIMEOUT_MS
        ),
        chunk_timeout_ms=_env_int(
            "ROBOCO_GROK_CHUNK_TIMEOUT_MS", _DEFAULT_CHUNK_TIMEOUT_MS
        ),
    )

    # Instructions = system prompt + the SessionStart briefing when mounted.
    candidates = [system_prompt, "/app/briefing.md"]
    instructions = [p for p in candidates if p and Path(p).exists()]

    config = build_opencode_config(
        _load_mcp_config(mcp_path),
        target,
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
