"""Render a Kimi CLI agent's runtime config + per-role rules at container start.

The ``roboco-agent-kimi`` image's entrypoint runs ``python -m
roboco.llm.providers.kimi_cli_config`` to write ``~/.kimi-code/config.toml``
(the login-managed provider/model/service blocks + telemetry/upgrade knobs +
the per-role ``[[permission.rules]]`` deny set + the bash-guard
``[[hooks]]`` wiring), ``~/.kimi-code/mcp.json`` (a near-passthrough of the
mounted Claude Code ``mcp-config.json`` — kimi's ``mcpServers`` schema is
Claude-identical, unlike grok's TOML or codex's config.toml translation), and
``~/.kimi-code/AGENTS.md`` (the composed role blueprint, grok's proven
additive-instruction-file mechanism). Keeping the translation in importable
Python (not a shell heredoc) makes it unit-testable, mirroring
:mod:`roboco.llm.providers.grok_cli_config` /
:mod:`roboco.llm.providers.gemini_cli_config`.

Parity notes (where Kimi's runtime model differs from grok's/gemini's):

  * **managed config blocks** — ``kimi login`` writes a fixed set of
    ``[providers."managed:kimi-code"]`` / ``[models."kimi-code/<alias>"]`` /
    ``[services.moonshot_*]`` blocks keyed to the account's subscription, not
    ours to discover per-container: this module renders them as constants
    (:data:`_KIMI_MODELS` etc.) instead of reading them off the mounted host
    config.toml (which the symlink step deliberately does NOT carry forward —
    only ``credentials/`` and ``oauth/`` are shared, see
    :mod:`roboco.llm.providers.kimi`'s module docstring). Per-alias
    fields not pinned down verbatim (``max_context_size``/``capabilities``
    for aliases beyond the live-verified ``k3`` @ 262144) are conservative,
    internally-consistent placeholders; the CLI's own ``config.invalid``
    error (verified to exit 1 with a clear message) is the fail-loud signal
    if a real account's managed block ever disagrees.
  * **permission model** — ``-p`` runs under unconditional auto-approval with
    no CLI-flag tool-removal equivalent (unlike grok's ``--disallowed-tools``/
    ``--deny``), so scoping is entirely the rendered ``[[permission.rules]]``
    array (``decision`` allow/deny/ask + a ``pattern`` glob, e.g.
    ``Bash(git push*)`` — evaluated deny-first/class-based, confirmed
    graceful: a denied tool call returns a permission error and the agent
    recovers, never cancels the run).
  * **hooks** — the SAME ``bash-guard-hook.sh`` the Claude/grok paths install
    (verified to accept kimi's Claude-schema snake_case ``PreToolUse`` stdin
    payload with no changes) is wired as a TOML ``[[hooks]]`` entry, ``command``
    pointed at ``kimi-bash-guard-wrapper.sh`` rather than the hook script
    directly: a ``[[hooks]]`` entry only tolerates ``event``/``matcher``/
    ``command``/``timeout`` (an ``env`` key silently drops the WHOLE hooks
    section — live-verified), so ``ROBOCO_GUARD_SKIP_GIT=1`` rides the
    wrapper's own ``export`` instead. Git ops are already denied gracefully by
    the permission rules above, so the hook is defense-in-depth for the
    exfil/identity-forgery categories the deny-rule globs don't reach.
    Hooks fire BEFORE permission rules (both fire on a deny-ruled call); a
    hook deny is ALSO graceful on kimi (unlike grok's run-cancelling hook
    deny), so this is a pure tripwire, never the sole boundary.
  * **system prompt** — ``$KIMI_CODE_HOME/AGENTS.md`` is additive (verified
    headless-honored) like grok's global ``AGENTS.md``; ``SYSTEM.md`` fully
    REPLACES the CLI's own built-in main-agent prompt and is deliberately not
    used here.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tomli_w

from roboco.agents_config import get_agent_role
from roboco.services.gateway.role_config import get_role_config

# kimi reads its global config from $KIMI_CODE_HOME/config.toml (default
# ~/.kimi-code — the agent's HOME is /home/agent). The container-local,
# WRITABLE home the entrypoint copies credentials/ into before this renders.
KIMI_CODE_HOME = Path.home() / ".kimi-code"
KIMI_CONFIG_PATH = KIMI_CODE_HOME / "config.toml"
KIMI_MCP_PATH = KIMI_CODE_HOME / "mcp.json"
# kimi loads $KIMI_CODE_HOME/AGENTS.md as a GLOBAL, ADDITIVE instruction file
# (live-verified headless) — the parity analogue of grok's global AGENTS.md.
KIMI_AGENTS_MD_PATH = KIMI_CODE_HOME / "AGENTS.md"
# The credential file the entrypoint copies in from the RO staging mount
# (see roboco.llm.providers.kimi); the auth preflight below reads its
# expires_at field directly — a plain JSON field, no JWT decode needed.
KIMI_CREDENTIALS_PATH = KIMI_CODE_HOME / "credentials" / "kimi-code.json"
# The composed role blueprint the orchestrator mounts into every agent container.
SYSTEM_PROMPT_PATH = Path(
    os.environ.get("ROBOCO_SYSTEM_PROMPT", "/app/system-prompt.md")
)
# The bash-guard PreToolUse hook script, baked into the agent base image —
# same script the Claude/grok paths install (verified to accept kimi's
# Claude-schema snake_case stdin payload unmodified).
BASH_GUARD_HOOK = os.environ.get(
    "ROBOCO_BASH_GUARD_HOOK", "/app/scripts/bash-guard-hook.sh"
)
# A [[hooks]] entry has no `env` field (live-verified: one present drops the
# WHOLE hooks section silently — see kimi_hooks_config below), so
# ROBOCO_GUARD_SKIP_GIT=1 rides this wrapper's own export instead. Baked into
# the kimi image alongside the entrypoint (docker/agent-kimi.Dockerfile).
KIMI_BASH_GUARD_WRAPPER = os.environ.get(
    "ROBOCO_KIMI_BASH_GUARD_WRAPPER", "/app/scripts/kimi-bash-guard-wrapper.sh"
)
# The entrypoint reads a small preflight ok/fail from `--check`'s exit code —
# no args file handoff is needed for kimi (unlike grok/codex/gemini's
# per-role flag-token file) since kimi's per-role scoping lives entirely in
# the rendered config.toml, not in CLI flags.

# --- Managed config (login-written; see module docstring) -------------------
_MANAGED_PROVIDER_KEY = "managed:kimi-code"
_MANAGED_BASE_URL = "https://api.kimi.com/coding/v1"
_MANAGED_DEFAULT_MODEL = "kimi-code/kimi-for-coding"
_MANAGED_OAUTH = {"storage": "file", "key": "oauth/kimi-code"}

# These blocks mirror what a real membership login writes into config.toml,
# field-for-field (captured live on 0.29.2, Moderato). The `model` value is
# the CLI-side managed name (`k3`, NOT the raw API id `kimi-k3`) — it is what
# the CLI sends on the wire, so an invented value breaks every run. Context
# sizes/capabilities are the login-written values too; a tier upgrade would
# raise k3's window server-side, and 262144 stays a safe client-side cap.
_MANAGED_CONTEXT = 262_144
_K3_CAPS = ["thinking", "always_thinking", "image_in", "video_in", "tool_use"]

_KIMI_MODELS: dict[str, dict[str, Any]] = {
    "kimi-code/k3": {
        "provider": _MANAGED_PROVIDER_KEY,
        "model": "k3",
        "max_context_size": _MANAGED_CONTEXT,
        "capabilities": _K3_CAPS,
        "display_name": "K3",
        "support_efforts": ["low", "high", "max"],
        "default_effort": "high",
    },
    "kimi-code/k3-256k": {
        "provider": _MANAGED_PROVIDER_KEY,
        "model": "k3-256k",
        "max_context_size": _MANAGED_CONTEXT,
        "capabilities": ["thinking", "always_thinking", "image_in", "tool_use"],
        "display_name": "K3-256k",
        "support_efforts": ["low", "high", "max"],
        "default_effort": "high",
    },
    "kimi-code/kimi-for-coding": {
        "provider": _MANAGED_PROVIDER_KEY,
        "model": "kimi-for-coding",
        "max_context_size": _MANAGED_CONTEXT,
        "capabilities": _K3_CAPS,
        "display_name": "K2.7 Coding",
    },
    "kimi-code/kimi-for-coding-highspeed": {
        "provider": _MANAGED_PROVIDER_KEY,
        "model": "kimi-for-coding-highspeed",
        "max_context_size": _MANAGED_CONTEXT,
        "capabilities": _K3_CAPS,
        "display_name": "K2.7 Coding Highspeed",
    },
}

# --- Permission model (deny-only; -p auto-approves everything else) --------
# Fleet-wide, every role: subagent ban (CEO, 2026-07-09) + no direct web
# (gated web stays MCP-side) + no cron + no Skill.
_FLEET_WIDE_DENY: tuple[str, ...] = (
    "Agent",
    "AgentSwarm",
    "WebSearch",
    "FetchURL",
    "CronCreate",
    "CronList",
    "CronDelete",
    "Skill",
)

# Roles that legitimately run a shell. Review / board roles never do — the
# same set grok_cli_config._BASH_ROLES / gemini_cli_config._BASH_ROLES use.
_BASH_ROLES = frozenset({"developer", "documenter", "cell_pm", "main_pm"})

# Git network/branch/history mutation, destructive shell, and raw
# package-manager commands — the SAME canonical pattern set as grok's
# --deny rules / codex's execpolicy rules. Denied gracefully (live-verified:
# the agent gets a permission error and recovers, the run doesn't cancel).
_GIT_MUTATE_DENY: tuple[str, ...] = (
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
_DESTRUCTIVE_DENY: tuple[str, ...] = ("Bash(rm -rf*)",)
_RAW_PM_DENY: tuple[str, ...] = (
    "Bash(uv run*)",
    "Bash(uv sync*)",
    "Bash(uv pip install*)",
    "Bash(uv pip uninstall*)",
    "Bash(uv lock*)",
    "Bash(uv add*)",
    "Bash(uv remove*)",
    "Bash(pip install*)",
    "Bash(pip3 install*)",
    "Bash(pip uninstall*)",
    "Bash(conda install*)",
    "Bash(conda create*)",
    "Bash(conda run*)",
    "Bash(poetry run*)",
    "Bash(poetry install*)",
    "Bash(poetry add*)",
)


def _allows_write(role: str) -> bool:
    """True if the role writes code (``role_config.allows_write``)."""
    try:
        return bool(get_role_config(role).allows_write)
    except KeyError:
        return False


def permission_rules_for_role(role: str) -> list[dict[str, str]]:
    """The ``[[permission.rules]]`` entries (as dicts) gating one role.

    Deny-only — ``-p`` auto-approves everything the rules below don't
    explicitly deny. Fleet-wide denies apply to every role; a non-bash-capable
    role gets a blanket ``Bash`` deny (closing the Write-via-Bash bypass, so
    it needs no command-scoped git/destructive/raw-PM rules underneath); a
    bash-capable role keeps the shell but gets the git-mutation/destructive/
    raw-PM prefix denies. Non-author roles (``role_config.allows_write`` is
    False) additionally get ``Write``/``Edit`` denied.
    """
    rules: list[dict[str, str]] = [
        {"pattern": pattern, "decision": "deny"} for pattern in _FLEET_WIDE_DENY
    ]
    if not _allows_write(role):
        rules.append({"pattern": "Write", "decision": "deny"})
        rules.append({"pattern": "Edit", "decision": "deny"})
    if role not in _BASH_ROLES:
        rules.append({"pattern": "Bash", "decision": "deny"})
        return rules
    for pattern in (*_DESTRUCTIVE_DENY, *_GIT_MUTATE_DENY, *_RAW_PM_DENY):
        rules.append({"pattern": pattern, "decision": "deny"})
    return rules


def kimi_hooks_config(
    hook_path: str = KIMI_BASH_GUARD_WRAPPER,
) -> list[dict[str, Any]]:
    """The ``[[hooks]]`` entries installing the bash-guard as a PreToolUse hook.

    A ``[[hooks]]`` entry only tolerates ``event``/``matcher``/``command``/
    ``timeout`` — an ``env`` key (or any other extra field) makes the CLI
    silently drop the WHOLE ``hooks`` section (live-verified: "Ignored
    invalid config ... hooks", run continues with NO hooks installed at
    all). ``ROBOCO_GUARD_SKIP_GIT=1`` therefore rides
    ``kimi-bash-guard-wrapper.sh`` (its own ``export`` before exec'ing the
    real hook) instead of an ``env`` field — git ops stay on the graceful
    deny rules above (see :func:`permission_rules_for_role`); the hook
    covers the credential-exfil/identity-forgery/env-dump categories the
    deny-rule globs don't reach. A hook deny is graceful on kimi
    (live-verified: "Blocked by PreToolUse hook", run continues) — a
    tripwire, never the sole boundary.
    """
    return [
        {
            "event": "PreToolUse",
            "matcher": "Bash",
            "command": hook_path,
        }
    ]


def render_config_toml(role: str) -> str:
    """Render ``config.toml``: managed blocks + telemetry/upgrade + per-role
    permission rules + the bash-guard hook.

    The managed provider/model/service blocks are constants (see module
    docstring) — never read off a mounted host file, since the symlink step
    deliberately carries forward only ``credentials/`` and ``oauth/`` (see
    :mod:`roboco.llm.providers.kimi`).
    """
    config: dict[str, Any] = {
        "default_model": _MANAGED_DEFAULT_MODEL,
        # Runtime self-update is suppressed at the image + env level
        # (KIMI_CODE_NO_AUTO_UPDATE=1); this belt-and-suspenders config knob
        # keeps the CLI from even checking, and telemetry is off fleet-wide.
        "telemetry": False,
        "upgrade": {"auto_install": False},
        "providers": {
            _MANAGED_PROVIDER_KEY: {
                "type": "kimi",
                "base_url": _MANAGED_BASE_URL,
                "oauth": dict(_MANAGED_OAUTH),
            }
        },
        "models": {alias: dict(fields) for alias, fields in _KIMI_MODELS.items()},
        "services": {
            "moonshot_search": {"type": "kimi", "oauth": dict(_MANAGED_OAUTH)},
            "moonshot_fetch": {"type": "kimi", "oauth": dict(_MANAGED_OAUTH)},
        },
        "permission": {"rules": permission_rules_for_role(role)},
        "hooks": kimi_hooks_config(),
    }
    return tomli_w.dumps(config)


def _load_mcp_config(path: str) -> dict[str, Any]:
    """Load the mounted mcp-config.json, tolerating a missing / invalid file."""
    try:
        with Path(path).open(encoding="utf-8") as fh:
            loaded = json.load(fh)
            return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def render_mcp_json(mcp_config: dict[str, Any]) -> str:
    """Render ``mcp.json`` — a near passthrough of the mounted mcp-config.json.

    Kimi's ``mcpServers`` schema (``command``/``args``/``env`` keyed by
    server name) is Claude-identical (live-verified: tool namespacing is the
    same ``mcp__<server>__<tool>`` shape too), so this is a structural
    reshape rather than a translation — unlike grok's TOML ``[mcp_servers]``
    or codex's config.toml block.
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
    return json.dumps({"mcpServers": servers}, indent=2)


def write_agents_md(
    *, source: Path = SYSTEM_PROMPT_PATH, dest: Path = KIMI_AGENTS_MD_PATH
) -> bool:
    """Install the mounted role blueprint as kimi's global AGENTS.md.

    Copies the composed prompt to ``$KIMI_CODE_HOME/AGENTS.md`` (additive,
    live-verified headless-honored — the grok-proven mechanism). Best-effort:
    returns False and writes nothing if the source is absent/unreadable, so a
    missing prompt never fails the render.
    """
    try:
        blueprint = source.read_text(encoding="utf-8")
    except OSError:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(blueprint, encoding="utf-8")
    return True


# --- Auth preflight (D2 branch (a): no refresh loop, a plain expiry read) --


def _parse_expires_at(value: object) -> datetime | None:
    """Parse ``credentials/kimi-code.json``'s ``expires_at`` field.

    A plain JSON field (no JWT decode, unlike codex's access-token exp
    claim) — tolerates either a unix-epoch number or an ISO-8601 string,
    since the exact wire representation wasn't pinned down beyond "a real
    expires_at sibling" in the spike.
    """
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def seconds_until_expiry(
    creds_path: Path = KIMI_CREDENTIALS_PATH, *, now: datetime | None = None
) -> float | None:
    """Seconds until the Kimi credential expires, or ``None`` if unreadable/absent."""
    try:
        data = json.loads(creds_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    expires_at = _parse_expires_at(data.get("expires_at"))
    if expires_at is None:
        return None
    return (expires_at - (now or datetime.now(UTC))).total_seconds()


def is_valid(
    creds_path: Path = KIMI_CREDENTIALS_PATH,
    *,
    skew_seconds: int = 0,
    now: datetime | None = None,
) -> bool:
    """True when the symlinked-in shared credential exists and has more than
    ``skew_seconds`` of life left. No orchestrator refresh loop exists for
    Kimi (D2 resolved rotation-with-short-reuse-grace over ONE shared RW
    auth mount, not per-container copies — each container self-refreshes
    through the CLI's own cross-process lock, see
    :mod:`roboco.llm.providers.kimi`); this is purely the entrypoint's
    fail-fast backstop."""
    remaining = seconds_until_expiry(creds_path, now=now)
    return remaining is not None and remaining > skew_seconds


def main(argv: list[str] | None = None) -> int:
    """Entrypoint: ``--check`` runs the auth preflight; else renders config.toml
    + mcp.json + AGENTS.md."""
    # Pass the module globals explicitly (not relying on is_valid's /
    # write_agents_md's own defaults, which bind at function-definition time
    # and would go stale if a caller reassigns the globals after import — a
    # real gap for e.g. a test module monkeypatching them post-import).
    args = argv if argv is not None else sys.argv[1:]
    if "--check" in args:
        return 0 if is_valid(KIMI_CREDENTIALS_PATH) else 1

    agent_id = os.environ.get("ROBOCO_AGENT_ID", "")
    mcp_path = os.environ.get("ROBOCO_MCP_CONFIG", "/app/mcp-config.json")
    role = get_agent_role(agent_id) or ""

    KIMI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    KIMI_CONFIG_PATH.write_text(render_config_toml(role), encoding="utf-8")
    KIMI_MCP_PATH.write_text(
        render_mcp_json(_load_mcp_config(mcp_path)), encoding="utf-8"
    )
    write_agents_md(source=SYSTEM_PROMPT_PATH, dest=KIMI_AGENTS_MD_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
