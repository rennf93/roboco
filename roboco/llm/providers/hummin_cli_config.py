"""Render a hummin CLI agent's runtime config + per-role tool allowlist.

The ``roboco-agent-hummin`` image's entrypoint runs ``python -m
roboco.llm.providers.hummin_cli_config`` to write two things:

  * ``~/.hummin/agent/settings.json`` — hummin's global settings (verified:
    ``getAgentDir()/settings.json`` in the hummin source), rendered ONLY when
    absent and merged-preserving otherwise. The container starts with no
    ``~/.hummin`` at all (key-based auth — no credential mount), so this
    seeds the fleet-safe defaults: telemetry/analytics off (parity with
    kimi's ``telemetry: false``) and ``defaultProjectTrust: "never"`` so a
    workspace clone's own project settings/resources are never loaded.
  * ``/tmp/roboco-hummin-env`` — a shell env file the entrypoint sources
    before the run. It carries ``ROBOCO_HUMMIN_TOOLS``, the per-role
    ``--tools`` allowlist string (see below).

What it deliberately does NOT render — two hummin runtime facts (verified
against the hummin source, vault gotcha ``hummin-mcp-and-rules-gaps``):

  * **No mcp.json.** hummin has no MCP client at all (its README: "No MCP.
    ... build an extension that adds MCP support"), so there is nothing to
    render the mounted ``mcp-config.json`` INTO. The gateway
    (``roboco-flow`` / ``roboco-do``) is unreachable from a hummin agent in
    V1; the env var rides along inert for the future explicit ``-e`` bridge
    extension. See :mod:`roboco.llm.providers.hummin`'s module docstring.
  * **No hooks / permission rules.** hummin has no PreToolUse hook surface
    (its "hooks" are extension lifecycle events, and V1 runs
    ``--no-extensions`` anyway) and no kimi-style ``[[permission.rules]]``
    engine. The kimi concept maps onto hummin's ``--tools`` STRICT
    allowlist instead (:func:`tools_for_role`): only built-in tools exist
    under ``--no-extensions`` (read/bash/edit/write), so a non-bash role
    simply doesn't get ``bash`` in its allowlist, and a non-author role
    doesn't get ``edit``/``write``. The bash-guard hook CANNOT be
    replicated — command-level screening inside bash has no hummin
    equivalent — which makes the tool-level boundary the ONLY boundary in
    V1 (a documented degradation vs the kimi/grok/codex paths, acceptable
    because a hummin agent also has no gateway verbs to abuse).

The ``--check`` mode is the auth preflight: it shells out to
``hummin auth check --provider zai --json`` and passes its exit code through
(verified in hummin's main.ts: ``ready`` → 0, ``not_ready`` → 1, ``invalid``
→ 2; a missing binary counts as 2). The entrypoint maps any non-zero to 78
(EX_CONFIG) so the orchestrator parks the provider.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from roboco.agents_config import get_agent_role
from roboco.services.gateway.role_config import get_role_config

# hummin reads its global config from $HUMMIN_CODING_AGENT_DIR/settings.json
# (default ~/.hummin/agent — the agent's HOME is /home/agent). Rendered only
# when absent; an existing file is merged-preserving (never clobbered).
HUMMIN_AGENT_DIR = Path(
    os.environ.get("HUMMIN_CODING_AGENT_DIR", str(Path.home() / ".hummin" / "agent"))
)
HUMMIN_SETTINGS_PATH = HUMMIN_AGENT_DIR / "settings.json"

# The env file the entrypoint sources to pick up the per-role --tools string.
HUMMIN_ENV_FILE = Path(
    os.environ.get("ROBOCO_HUMMIN_ENV_FILE")
    or Path(tempfile.gettempdir()) / "roboco-hummin-env"
)

# hummin's built-in tool surface (the ONLY tools that exist under
# --no-extensions — no extension/custom tools are loadable in V1).
_READ_TOOLS = ("read",)
_WRITE_TOOLS = ("edit", "write")
_BASH_TOOLS = ("bash",)

# Roles that legitimately run a shell. Review / board roles never do — the
# same set grok_cli_config / kimi_cli_config agree on.
_BASH_ROLES = frozenset({"developer", "documenter", "cell_pm", "main_pm"})


def _allows_write(role: str) -> bool:
    """True if the role writes code (``role_config.allows_write``)."""
    try:
        return bool(get_role_config(role).allows_write)
    except KeyError:
        return False


def tools_for_role(role: str) -> str:
    """The ``--tools`` allowlist string for one role (kimi's permission
    semantics mapped onto hummin's strict allowlist).

    Every role gets ``read``. Author roles (``role_config.allows_write``)
    add ``edit``/``write``; bash-capable roles (the same ``_BASH_ROLES`` set
    every other CLI config module uses) add ``bash``. Under ``--tools``
    hummin starts from the EMPTY set and only allows what's listed, so an
    omitted tool is exactly kimi's deny — and since no extension/custom
    tools exist under ``--no-extensions``, the built-in surface above is the
    complete universe to scope.
    """
    tools = *_READ_TOOLS, *(_WRITE_TOOLS if _allows_write(role) else ())
    if role in _BASH_ROLES:
        tools = (*tools, *_BASH_TOOLS)
    return ",".join(tools)


def render_settings_defaults(existing: dict[str, object] | None) -> dict[str, object]:
    """The fleet-safe settings defaults, merged over ``existing`` (if any).

    Merge-preserving: an operator-set key in a pre-existing settings.json
    always wins (the container has no ``~/.hummin`` in practice — key auth —
    so this only matters for a hand-prepared image; keep it honest anyway).
    """
    merged: dict[str, object] = dict(existing or {})
    merged.setdefault("enableInstallTelemetry", False)
    merged.setdefault("enableAnalytics", False)
    merged.setdefault("defaultProjectTrust", "never")
    return merged


def write_env_file(path: Path = HUMMIN_ENV_FILE, *, role: str | None = None) -> None:
    """Write the sourced env file carrying the per-role ``--tools`` string."""
    if role is None:
        role = get_agent_role(os.environ.get("ROBOCO_AGENT_ID", "")) or ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'ROBOCO_HUMMIN_TOOLS="{tools_for_role(role)}"\n', encoding="utf-8")


def auth_preflight() -> int:
    """Run hummin's own auth preflight; return its raw exit code.

    ``hummin auth check --provider zai --json`` (verified exit-code mapping:
    0 ready / 1 not_ready / 2 invalid). The entrypoint maps non-zero to 78.
    A missing/unrunnable binary returns 2 (treat it as invalid rather than
    pretending readiness).
    """
    try:
        return subprocess.run(
            ["hummin", "auth", "check", "--provider", "zai", "--json"],
            check=False,
        ).returncode
    except OSError:
        return 2


def main(argv: list[str] | None = None) -> int:
    """Entrypoint: ``--check`` runs the auth preflight; else renders
    settings.json defaults + the per-role tools env file."""
    args = argv if argv is not None else sys.argv[1:]
    if "--check" in args:
        return auth_preflight()

    existing: dict[str, object] | None = None
    with contextlib.suppress(OSError, ValueError):
        loaded = json.loads(HUMMIN_SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded
    HUMMIN_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    HUMMIN_SETTINGS_PATH.write_text(
        json.dumps(render_settings_defaults(existing), indent=2), encoding="utf-8"
    )
    # Pass the module global explicitly (not relying on write_env_file's own
    # default, which binds at function-definition time and would go stale if
    # a caller reassigns the global after import — a real gap for e.g. a
    # test module monkeypatching it post-import).
    write_env_file(HUMMIN_ENV_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
