"""Render a Codex CLI agent's runtime config + per-role flags at container start.

The ``roboco-agent-codex`` image's entrypoint runs ``python -m
roboco.llm.providers.codex_cli_config`` to turn the mounted Claude Code
``mcp-config.json`` into ``~/.codex/config.toml`` (``[mcp_servers.<name>]``),
write the execpolicy deny rules the git-push/package-manager parity needs, and
compose the combined system+task prompt ``codex exec`` runs against. Keeping
the translation in importable Python (not a shell heredoc) makes it
unit-testable, mirroring :mod:`roboco.llm.providers.grok_cli_config`.

Parity notes (where Codex's runtime model differs from grok's / Claude's):

  * **subagents** — fleet-wide ban (CEO, 2026-07-09): ``config.toml``'s
    ``[agents]`` table (default ``enabled = true``) is rendered with
    ``enabled = false`` unconditionally, the parity analogue of grok's
    per-role ``--disallowed-tools Agent`` and gemini's
    ``experimental.enableAgents=false`` — a single global switch here too,
    not a per-role rule, since Codex has no per-role tool-removal flag either.
  * **tool removal** — the Codex CLI exposes no per-built-in-tool
    allow/disallow flags (unlike grok's ``--disallowed-tools``). Tool scoping
    is coarser: a ``--sandbox`` level per role (see :func:`sandbox_level_for_role`)
    plus the execpolicy rules file below.
  * **git / package-manager mutation** — codex's execpolicy is Starlark
    ``prefix_rule(pattern=[...], decision=...)`` with only ``allow`` /
    ``forbidden`` decisions available headless (a ``prompt`` decision would
    block on an approval prompt that never arrives in a one-shot run). One
    shared ``~/.codex/rules/default.rules`` file encodes the same git-mutation
    / destructive / raw-package-manager denials grok expresses as
    ``--deny`` rules — applied to every role (the sandbox level, not a
    per-role rules variant, is what actually differs role to role).
  * **system prompt** — the Codex CLI has no verified global instruction-file
    mechanism (unlike grok's ``~/.grok/AGENTS.md``), so the composed role
    blueprint is prepended to the task prompt itself and the RESULT is what
    ``codex exec`` receives as its positional prompt argument (see
    :func:`render_combined_prompt`). The prompt-injection guard in the
    entrypoint still screens only the raw task prompt (the blueprint is
    already trusted), matching grok's guard scope.
  * **no hooks** — Codex's ``config.toml`` exposes no hook mechanism in the
    verified build facts, so neither the bash-guard exfiltration hook nor the
    Fable honesty-nudge hook is ported here (a V1 gap vs. the grok path).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import tomli_w

from roboco.agents_config import get_agent_role

# codex reads its global config from $HOME/.codex/config.toml.
CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
# Execpolicy rules file (Starlark prefix_rule, allow/forbidden only).
CODEX_RULES_DIR = Path.home() / ".codex" / "rules"
CODEX_RULES_PATH = CODEX_RULES_DIR / "default.rules"
# The composed role blueprint the orchestrator mounts into every agent container.
SYSTEM_PROMPT_PATH = Path(
    os.environ.get("ROBOCO_SYSTEM_PROMPT", "/app/system-prompt.md")
)
# The combined system+task prompt the entrypoint feeds to `codex exec` as its
# positional argument (see the module docstring — no verified system-prompt
# file mechanism exists for Codex, so the blueprint travels IN the prompt).
CODEX_PROMPT_PATH = Path(
    os.environ.get("ROBOCO_CODEX_PROMPT_FILE")
    or Path(tempfile.gettempdir()) / "roboco-codex-prompt.txt"
)
# The entrypoint reads the computed per-role flags (one token per line) from
# this file, mirroring grok_cli_config's GROK_ARGS_PATH handoff.
CODEX_ARGS_PATH = Path(
    os.environ.get("ROBOCO_CODEX_ARGS_FILE")
    or Path(tempfile.gettempdir()) / "roboco-codex-args"
)

# The gateway pair MUST come up or the agent has no verb surface at all — set
# required=true so a gateway-init failure fails the codex session fast instead
# of silently running with no tools. Every other MCP server (git-readonly,
# optimal, docs, playwright) is best-effort.
_REQUIRED_MCP_SERVERS = frozenset({"roboco-flow", "roboco-do"})

# The CLI's default MCP startup timeout (10s) is too tight for a cold uv wheel
# cache (first spawn after an image rebuild — see the identical rationale in
# roboco.runtime.orchestrator._generate_mcp_config's UV_PROJECT_ENVIRONMENT
# comment): a required server not yet ready at 10s fail-fast-aborts the whole
# session. Widened per required server so a slow-but-working cold start
# doesn't get treated as a dead gateway.
_REQUIRED_MCP_STARTUP_TIMEOUT_SEC = 30

# Only `developer` gets a writable sandbox in Codex V1 — narrower than grok's
# per-role `allows_write` (role_config says documenter also writes). Documenter
# writes ride the roboco-docs MCP server (a network call, not a local sandboxed
# file edit), so a read-only sandbox does not block its actual job; qa /
# pr_reviewer / cell_pm / main_pm never write code either way. Loosen this set
# if a role's real workflow needs local file writes under Codex.
_WORKSPACE_WRITE_ROLES = frozenset({"developer"})

_SANDBOX_WORKSPACE_WRITE = "workspace-write"
_SANDBOX_READ_ONLY = "read-only"

# Deny parity with grok's --deny rules (roboco/llm/providers/grok_cli_config's
# _GIT_MUTATE_DENY / _DESTRUCTIVE_DENY / _RAW_PM_DENY), expressed as execpolicy
# command prefixes instead of glob strings. Applied via ONE shared rules file
# for every role (the sandbox level is what varies per role, not this list) —
# skipped a per-role rules variant, add one if a role needs a narrower/broader
# command surface than the rest.
_GIT_MUTATE_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("git", "push"),
    ("git", "fetch"),
    ("git", "pull"),
    ("git", "clone"),
    ("git", "commit"),
    ("git", "remote"),
    ("git", "reset"),
    ("git", "ls-remote"),
    ("git", "checkout"),
    ("git", "merge"),
    ("git", "rebase"),
    ("git", "cherry-pick"),
    ("git", "revert"),
    ("git", "update-ref"),
    ("git", "tag", "-d"),
    ("git", "reflog", "delete"),
)
_DESTRUCTIVE_PREFIXES: tuple[tuple[str, ...], ...] = (("rm", "-rf"),)
_RAW_PM_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("uv", "run"),
    ("uv", "sync"),
    ("uv", "pip", "install"),
    ("uv", "pip", "uninstall"),
    ("uv", "lock"),
    ("uv", "add"),
    ("uv", "remove"),
    ("pip", "install"),
    ("pip3", "install"),
    ("pip", "uninstall"),
    ("conda", "install"),
    ("conda", "create"),
    ("conda", "run"),
    ("poetry", "run"),
    ("poetry", "install"),
    ("poetry", "add"),
)


def render_config_toml(mcp_config: dict[str, Any]) -> str:
    """Translate Claude Code ``mcpServers`` into codex's config.toml.

    ``{"command": "uv", "args": [...], "env": {...}}`` becomes a
    ``[mcp_servers.<name>]`` table with the same fields, plus ``required =
    true`` + ``startup_timeout_sec = 30`` for the gateway pair (``roboco-flow``
    / ``roboco-do``) so a gateway-init failure fails the codex session fast
    without tripping on a cold uv wheel cache (see
    ``_REQUIRED_MCP_STARTUP_TIMEOUT_SEC``). Always carries a top-level
    ``[agents]`` table disabling Codex's native subagents (fleet-wide ban,
    CEO 2026-07-09 — parity with grok's ``--disallowed-tools Agent`` and
    gemini's ``experimental.enableAgents=false``): a global switch, not
    per-role, so it renders unconditionally even with no MCP servers at all.
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
        if str(name) in _REQUIRED_MCP_SERVERS:
            block["required"] = True
            block["startup_timeout_sec"] = _REQUIRED_MCP_STARTUP_TIMEOUT_SEC
        servers[str(name)] = block
    config: dict[str, Any] = {"agents": {"enabled": False}}
    if servers:
        config["mcp_servers"] = servers
    return tomli_w.dumps(config)


def sandbox_level_for_role(role: str) -> str:
    """The ``--sandbox`` level for a role (see ``_WORKSPACE_WRITE_ROLES``)."""
    return (
        _SANDBOX_WORKSPACE_WRITE
        if role in _WORKSPACE_WRITE_ROLES
        else _SANDBOX_READ_ONLY
    )


def codex_cli_args_for_role(role: str) -> list[str]:
    """The per-role ``codex exec`` flag tokens (excludes ``-m``/``--json``).

    Role-invariant flags (``--json``, the model) are hardcoded in the
    entrypoint shell script instead of here, since they never vary by role —
    only the sandbox level does.
    """
    return ["--sandbox", sandbox_level_for_role(role), "--skip-git-repo-check"]


def codex_cli_args(agent_id: str) -> list[str]:
    """The per-role codex flags for an agent, resolving its role from the id."""
    return codex_cli_args_for_role(get_agent_role(agent_id) or "")


def _prefix_rule(prefix: tuple[str, ...], decision: str = "forbidden") -> str:
    args = ", ".join(json.dumps(token) for token in prefix)
    return f'prefix_rule(pattern = [{args}], decision = "{decision}")'


def render_execpolicy_rules() -> str:
    """The Starlark execpolicy rules text (git-mutation + destructive + raw-PM).

    ``allow`` / ``forbidden`` decisions only — a ``prompt`` decision blocks on
    an approval prompt that never arrives in a headless ``codex exec`` turn,
    failing the turn instead of gracefully adapting.
    """
    lines = [
        "# Generated by roboco.llm.providers.codex_cli_config — do not hand-edit.",
        "# Git network/branch/history mutation: agents commit/push via the",
        "# gateway verbs, never raw git.",
    ]
    lines += [_prefix_rule(p) for p in _GIT_MUTATE_PREFIXES]
    lines.append("# Destructive shell.")
    lines += [_prefix_rule(p) for p in _DESTRUCTIVE_PREFIXES]
    lines.append("# Raw package-manager / lockfile commands — use `make` instead.")
    lines += [_prefix_rule(p) for p in _RAW_PM_PREFIXES]
    return "\n".join(lines) + "\n"


def write_execpolicy_rules(*, dest: Path = CODEX_RULES_PATH) -> None:
    """Write the shared execpolicy rules file (always; content is generated)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_execpolicy_rules(), encoding="utf-8")


def render_combined_prompt(system_prompt: str, task_prompt: str) -> str:
    """Compose the blueprint + task prompt into one ``codex exec`` argument.

    No verified Codex system-prompt-file mechanism exists (see module
    docstring), so the trusted blueprint is prepended to the (guard-screened,
    by the entrypoint) task prompt rather than mounted separately.
    """
    system_prompt = system_prompt.strip()
    task_prompt = task_prompt.strip()
    if not system_prompt:
        return task_prompt
    if not task_prompt:
        return system_prompt
    return f"{system_prompt}\n\n---\n\n{task_prompt}"


def write_combined_prompt(
    *,
    task_prompt: str,
    source: Path = SYSTEM_PROMPT_PATH,
    dest: Path = CODEX_PROMPT_PATH,
) -> bool:
    """Write the combined prompt file; returns True iff a blueprint was found.

    A missing/unreadable blueprint degrades to the task prompt alone rather
    than failing the render.
    """
    try:
        blueprint = source.read_text(encoding="utf-8")
    except OSError:
        blueprint = ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_combined_prompt(blueprint, task_prompt), encoding="utf-8")
    return bool(blueprint)


def _load_mcp_config(path: str) -> dict[str, Any]:
    """Load the mounted mcp-config.json, tolerating a missing / invalid file."""
    try:
        with Path(path).open(encoding="utf-8") as fh:
            loaded = json.load(fh)
            return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> int:
    """Entrypoint: write config.toml + execpolicy rules + prompt + per-role args."""
    agent_id = os.environ.get("ROBOCO_AGENT_ID", "")
    mcp_path = os.environ.get("ROBOCO_MCP_CONFIG", "/app/mcp-config.json")

    CODEX_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG_PATH.write_text(
        render_config_toml(_load_mcp_config(mcp_path)), encoding="utf-8"
    )
    write_execpolicy_rules()
    write_combined_prompt(task_prompt=os.environ.get("ROBOCO_INITIAL_PROMPT", ""))
    CODEX_ARGS_PATH.write_text(
        "\n".join(codex_cli_args(agent_id)) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
