"""Render a Gemini CLI agent's runtime config + per-role policy at container start.

The ``roboco-agent-gemini`` image's entrypoint runs ``python -m
roboco.llm.providers.gemini_cli_config`` to turn the mounted Claude Code
``mcp-config.json`` into ``~/.gemini/settings.json`` (``mcpServers`` +
auth/experimental/advanced flags) and to render a per-role TOML Policy Engine
file at ``~/.gemini/policies/roboco.toml``. Keeping the translation in
importable Python (not a shell heredoc) makes it unit-testable.

Unlike the grok CLI (native ``--disallowed-tools`` / ``--deny`` flags), the
Gemini CLI has no per-invocation tool-removal flag: tool scoping is expressed
entirely through the TOML Policy Engine (``decision = "deny"`` rules matched
by ``toolName`` / ``commandPrefix``) plus two ``settings.json`` switches. This
module's rules are the parity analogue of ``grok_cli_config``'s
``_disallowed_tools`` / ``_deny_rules``:

  * **subagents** — fleet-wide ban via ``settings.json``'s
    ``experimental.enableAgents = false`` (no per-role policy rule needed; this
    is a single global switch, unlike grok's per-role ``--disallowed-tools Agent``).
  * **editing** — a role that doesn't write code (``role_config.allows_write``
    is False) gets both edit tools (``write_file``, ``replace``) denied.
  * **shell** — a role that never runs a shell (review / board) gets
    ``run_shell_command`` denied outright — nothing left to gate underneath it.
  * **git mutation** — a bash-capable role keeps the shell tool, but a
    ``commandPrefix`` deny rule per git-mutating verb blocks raw git network /
    branch / history ops (agents commit / push through the gateway verbs).
  * **destructive / raw package manager** — ``rm -rf`` and the raw
    uv/pip/conda/poetry invocations are denied the same way (CEO direction:
    use ``make`` instead).

``--approval-mode yolo`` (headless full auto-approval — the CLI's own
``ask_user`` policy auto-denies in a headless run, so an unapproved run would
never progress) is universal across roles, unlike grok's per-role reasoning
``--effort``: the spike found no verified reasoning-effort knob for the Gemini
CLI, so none is rendered here.

No hooks are installed for Gemini (contrast ``grok_cli_config``'s
``write_grok_hooks`` / ``write_grok_fable_hooks``): the spike found no verified
hook mechanism on the Gemini CLI, so the exfil-pattern bash-guard and
Fable-mode honesty-nudge are NOT ported in V1 — the Policy Engine deny rules
above are the only enforcement layer. The role blueprint reaches the model via
``~/.gemini/GEMINI.md``, the CLI's hierarchical user-memory file (the
parity analogue of grok's global ``AGENTS.md``).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import tomli_w

from roboco.agents_config import get_agent_role
from roboco.services.gateway.role_config import get_role_config

# gemini reads its global settings from ``$HOME/.gemini/settings.json`` (the
# agent's HOME is ``/home/agent``; the host OAuth credential is staged
# alongside it — see roboco.llm.providers.gemini for the copy-not-symlink
# rationale).
GEMINI_SETTINGS_PATH = Path.home() / ".gemini" / "settings.json"
# gemini loads ``$HOME/.gemini/GEMINI.md`` as a hierarchical user-memory file
# (on top of any project-level GEMINI.md under cwd) regardless of --cwd — the
# parity analogue of the grok path's global AGENTS.md. This is how the RoboCo
# role blueprint becomes gemini's system prompt without writing into (and
# polluting) the agent's git workspace.
GEMINI_MEMORY_PATH = Path.home() / ".gemini" / "GEMINI.md"
# The composed role blueprint the orchestrator mounts into every agent container.
SYSTEM_PROMPT_PATH = Path(
    os.environ.get("ROBOCO_SYSTEM_PROMPT", "/app/system-prompt.md")
)
# The TOML Policy Engine reads every ``*.toml`` file under this directory.
GEMINI_POLICIES_DIR = Path.home() / ".gemini" / "policies"
_POLICY_FILE_NAME = "roboco.toml"

# The auth mode a headless run must declare in settings.json, else the CLI
# refuses with exit 41 instead of silently using the mounted OAuth credential
# (verified fact). ``oauth-personal`` is the CLI's "Login with Google"
# personal-OAuth AuthType — the free-tier / subscription-style mode this
# provider mounts a credential for (contrast ``gemini-api-key`` / ``vertex-ai``,
# neither of which apply here).
_AUTH_SELECTED_TYPE = "oauth-personal"

# The entrypoint reads the computed per-role CLI flags (one token per line)
# from this file. Defaults under the system temp dir (not a hardcoded /tmp
# literal) — mirrors grok_cli_config.GROK_ARGS_PATH.
GEMINI_ARGS_PATH = Path(
    os.environ.get("ROBOCO_GEMINI_ARGS_FILE")
    or Path(tempfile.gettempdir()) / "roboco-gemini-args"
)

# Roles that legitimately run a shell. Review / board roles never do — mirrors
# grok_cli_config._BASH_ROLES exactly.
_BASH_ROLES = frozenset({"developer", "documenter", "cell_pm", "main_pm"})

# Gemini CLI built-in tool ids gated by the Policy Engine.
_TOOL_SHELL = "run_shell_command"
_EDIT_TOOLS = ("write_file", "replace")

# Raw git network / branch / history mutation is gateway-mediated (the commit /
# open_pr verbs); agents never run these via raw shell. Denied for every
# bash-capable role — the same set the grok path's ``_GIT_MUTATE_DENY`` blocks.
_GIT_MUTATE_PREFIXES: tuple[str, ...] = (
    "git push",
    "git fetch",
    "git pull",
    "git clone",
    "git commit",
    "git remote",
    "git reset",
    "git ls-remote",
    "git checkout",
    "git merge",
    "git rebase",
    "git cherry-pick",
    "git revert",
    "git update-ref",
    "git tag -d",
    "git reflog delete",
)
_DESTRUCTIVE_PREFIXES: tuple[str, ...] = ("rm -rf",)
# Raw package-manager / test-runner commands — use the Makefile (CEO direction),
# mirroring grok_cli_config._RAW_PM_DENY.
_RAW_PM_PREFIXES: tuple[str, ...] = (
    "uv run",
    "uv sync",
    "uv pip install",
    "uv pip uninstall",
    "uv lock",
    "uv add",
    "uv remove",
    "pip install",
    "pip3 install",
    "pip uninstall",
    "conda install",
    "conda create",
    "conda run",
    "poetry run",
    "poetry install",
    "poetry add",
)


def _allows_write(role: str) -> bool:
    """True if the role writes code (``role_config.allows_write``)."""
    try:
        return bool(get_role_config(role).allows_write)
    except KeyError:
        return False


def policy_rules_for_role(role: str) -> list[dict[str, Any]]:
    """The Policy Engine ``[[rule]]`` entries (as dicts) gating one role.

    Deny-only (mirrors grok's native ``--deny``): nothing here grants
    permission — ``--approval-mode yolo`` auto-approves everything the Policy
    Engine doesn't explicitly deny. Priority is uniform (evaluation order
    doesn't matter between denies); a shell-less role gets one blanket
    ``run_shell_command`` deny and nothing else, since there is no command
    left to gate underneath it.
    """
    rules: list[dict[str, Any]] = []
    if not _allows_write(role):
        rules.extend(
            {"toolName": tool, "decision": "deny", "priority": 10}
            for tool in _EDIT_TOOLS
        )
    if role not in _BASH_ROLES:
        rules.append({"toolName": _TOOL_SHELL, "decision": "deny", "priority": 10})
        return rules
    for prefix in (*_DESTRUCTIVE_PREFIXES, *_GIT_MUTATE_PREFIXES, *_RAW_PM_PREFIXES):
        rules.append(
            {
                "toolName": _TOOL_SHELL,
                "commandPrefix": prefix,
                "decision": "deny",
                "priority": 20,
            }
        )
    return rules


def render_policy_toml(role: str) -> str:
    """Render the Policy Engine TOML for a role; ``""`` when it has no rules."""
    rules = policy_rules_for_role(role)
    return tomli_w.dumps({"rule": rules}) if rules else ""


def render_settings_json(mcp_config: dict[str, Any]) -> dict[str, Any]:
    """Translate Claude Code ``mcpServers`` + fixed flags into gemini's settings.json.

    ``{"command": "uv", "args": [...], "env": {...}}`` becomes the identically
    shaped ``mcpServers.<name>`` entry gemini's own schema uses. The three
    fixed flags are DESIGN DECISIONS, not per-role: ``selectedType`` makes
    headless auth resolve against the mounted OAuth credential instead of
    exiting 41; ``enableAgents=false`` is the fleet-wide subagent ban (CEO,
    2026-07-09); ``autoConfigureMemory=false`` pins Node's heap sizing so the
    CLI doesn't try to auto-size against a shared host (the Dockerfile sets an
    explicit ``--max-old-space-size`` instead).
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
    return {
        "mcpServers": servers,
        "security": {"auth": {"selectedType": _AUTH_SELECTED_TYPE}},
        "experimental": {"enableAgents": False},
        "advanced": {"autoConfigureMemory": False},
    }


def gemini_cli_args() -> list[str]:
    """The ``gemini -p`` flag tokens (excludes ``-p``/``-m``/``--cwd``).

    Universal across every role — ``--approval-mode yolo`` (headless
    auto-approval); tool scoping lives entirely in the rendered Policy Engine
    / settings.json (see :func:`policy_rules_for_role`), not in a CLI flag,
    unlike grok's per-role ``grok_cli_args_for_role``.
    """
    return ["--approval-mode", "yolo"]


def _load_mcp_config(path: str) -> dict[str, Any]:
    """Load the mounted mcp-config.json, tolerating a missing / invalid file."""
    try:
        with Path(path).open(encoding="utf-8") as fh:
            loaded = json.load(fh)
            return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_gemini_memory(
    *, source: Path = SYSTEM_PROMPT_PATH, dest: Path = GEMINI_MEMORY_PATH
) -> bool:
    """Install the mounted role blueprint as gemini's global memory file.

    Copies the composed prompt to ``~/.gemini/GEMINI.md``. Best-effort: returns
    False and writes nothing if the source is absent / unreadable, so a missing
    prompt never fails the render.
    """
    try:
        blueprint = source.read_text(encoding="utf-8")
    except OSError:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(blueprint, encoding="utf-8")
    return True


def write_policy_toml(role: str, *, policies_dir: Path = GEMINI_POLICIES_DIR) -> bool:
    """Write the role's Policy Engine TOML; returns False (no-op) if it has no rules."""
    rendered = render_policy_toml(role)
    if not rendered:
        return False
    policies_dir.mkdir(parents=True, exist_ok=True)
    (policies_dir / _POLICY_FILE_NAME).write_text(rendered, encoding="utf-8")
    return True


def main() -> int:
    """Entrypoint: write ``~/.gemini/settings.json`` + GEMINI.md + policy TOML."""
    agent_id = os.environ.get("ROBOCO_AGENT_ID", "")
    mcp_path = os.environ.get("ROBOCO_MCP_CONFIG", "/app/mcp-config.json")
    role = get_agent_role(agent_id) or ""

    GEMINI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEMINI_SETTINGS_PATH.write_text(
        json.dumps(render_settings_json(_load_mcp_config(mcp_path)), indent=2),
        encoding="utf-8",
    )
    # Pass the module globals explicitly (not relying on write_gemini_memory /
    # write_policy_toml's own defaults, which bind at function-definition time
    # and would go stale if a caller reassigns the globals after import — a
    # real gap for e.g. a test module monkeypatching them post-import).
    write_gemini_memory(source=SYSTEM_PROMPT_PATH, dest=GEMINI_MEMORY_PATH)
    write_policy_toml(role, policies_dir=GEMINI_POLICIES_DIR)
    GEMINI_ARGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEMINI_ARGS_PATH.write_text("\n".join(gemini_cli_args()) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
