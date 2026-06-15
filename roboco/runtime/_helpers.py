"""Pure helpers and constants for the runtime orchestrator.

This module holds the dependency-light, side-effect-free pieces that used to
live at the top of ``orchestrator.py``: Docker/host-path constants, the
role->image map, small frozen dataclasses, and pure task/path helper
functions. None of these touch the database, Docker, or the running event
loop, which makes them trivially unit-testable in isolation.

``orchestrator.py`` re-imports every public name defined here, so existing
imports such as ``from roboco.runtime.orchestrator import _is_coordination_task``
continue to work unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from roboco.agents_config import get_agent_role, get_agent_team
from roboco.config import settings
from roboco.models.runtime import MODEL_MAP
from roboco.seeds.initial_data import AGENT_UUIDS

if TYPE_CHECKING:
    from roboco.models.runtime import SpawnGitContext

# Docker configuration
AGENT_NETWORK = "roboco_default"
AGENT_BASE_IMAGE = "roboco-agent-base"

# Port on which each agent's Claude Code SDK server listens inside its container.
# Referenced by write-hooks (_finalize_spawn_session, _sweep_token_snapshots,
# _sweep_budget_exceeded) to build the SDK health/usage URL.
SDK_PORT: int = 9000

# Rate-limit recovery probe: a free, unmetered liveness call confirms a
# provider has stopped rate-limiting us before parked agents are resumed.
# Listing models / tags costs no tokens; a non-429 response means lifted.
_ANTHROPIC_PROBE_BASE = "https://api.anthropic.com"
_PROBE_TIMEOUT_SECONDS = 10.0
_HTTP_TOO_MANY_REQUESTS = 429
# Consecutive failed recovery probes before the CEO is notified once per episode.
_CEO_NOTIFY_THRESHOLD = 10

# The intake (prompter) agent: a single seeded, board-adjacent interviewer.
# Unlike delivery agents it is never dispatched and runs ONE persistent
# container at a time (single CEO -> one live chat). See the INTAKE section
# in orchestrator.py and roboco/agent_sdk/intake_main.py.
INTAKE_AGENT_ID = "intake-1"

# Role -> Image mapping
# Specialized images extend the base with role-specific tools
AGENT_IMAGES: dict[str, str] = {
    # Backend
    "be-dev-1": "roboco-agent-dev-be",
    "be-dev-2": "roboco-agent-dev-be",
    "be-qa": "roboco-agent-qa-be",
    "be-pm": "roboco-agent-pm",
    "be-doc": "roboco-agent-doc",
    # Frontend
    "fe-dev-1": "roboco-agent-dev-fe",
    "fe-dev-2": "roboco-agent-dev-fe",
    "fe-qa": "roboco-agent-qa-fe",
    "fe-pm": "roboco-agent-pm",
    "fe-doc": "roboco-agent-doc",
    # UX/UI
    "ux-dev-1": "roboco-agent-ux",
    "ux-dev-2": "roboco-agent-ux",
    "ux-qa": "roboco-agent-ux",  # Uses same as dev for now
    "ux-pm": "roboco-agent-pm",
    "ux-doc": "roboco-agent-doc",
    # Board
    "main-pm": "roboco-agent-pm",
    "product-owner": "roboco-agent-pm",
    "head-marketing": "roboco-agent-pm",
    "auditor": "roboco-agent-pm",
    # Intake - persistent Agent-SDK driver, not a one-shot `claude -p`.
    INTAKE_AGENT_ID: "roboco-agent-prompter",
}


def get_agent_image(agent_id: str) -> str:
    """Get the Docker image for an agent."""
    return AGENT_IMAGES.get(agent_id, AGENT_BASE_IMAGE)


# When running in a container, we need host paths for volume mounts.
# These can be overridden via environment variables.
CLAUDE_AUTH_HOST_PATH = os.environ.get(
    "ROBOCO_HOST_CLAUDE_DIR",
    str(Path.home() / ".claude"),
)
PROJECT_HOST_PATH = os.environ.get("ROBOCO_HOST_PROJECT_DIR", "")
DATA_HOST_PATH = os.environ.get("ROBOCO_HOST_DATA_DIR", "")


@dataclass(frozen=True)
class _SlaBreach:
    """Per-(role, state) SLA breach payload for _escalate_sla_breach."""

    task_id: str
    role: str
    status: str
    age_seconds: int
    sla_seconds: int


@dataclass(frozen=True)
class _IntakeRunSpec:
    """Inputs for ``_build_intake_run_cmd``, bundled to keep the signature small."""

    container_name: str
    image: str
    hosts: dict[str, str | None]
    session_id: str
    cwd: str
    cli_model: str
    api_url: str
    provider_base_url: str | None
    provider_auth_token: str | None


def _read_project_slug(task: dict[str, Any]) -> str | None:
    """Extract project slug from a task payload shape-tolerantly."""
    slug = task.get("project_slug")
    if slug:
        return str(slug)
    project = task.get("project") or {}
    inner = project.get("slug") if isinstance(project, dict) else None
    return str(inner) if inner else None


def _is_coordination_task(task: dict[str, Any]) -> bool:
    """True for a board/fan-out task that carries a product but no repo of its own.

    Such a task does no git work itself: its cell subtasks each resolve a real
    project from the product's cell->project map (see TaskCreate's
    project-or-product invariant and migration 018). It therefore has no
    project_slug, branch_name, or git token, and must NOT be git-gated at the
    spawn-readiness or stuck-detection checks the way a code task is. A task with
    neither a project nor a product is genuinely unroutable and stays gated.
    """
    return not task.get("project_id") and bool(task.get("product_id"))


# A branch is auto-created only at CLAIM (the claimed->in_progress transition).
# Before that - while a task is still pending/backlog awaiting first dispatch -
# it legitimately has no branch_name, so the readiness / stuck / spawn checks
# must NOT treat a missing branch as a defect. These are the only states where
# a code task is expected to already own a branch.
_BRANCH_EXPECTED_STATES: frozenset[str] = frozenset(
    {"claimed", "in_progress", "verifying"}
)


def _branch_is_expected(task: dict[str, Any]) -> bool:
    """True iff this task should already have a branch_name.

    A branch only exists at/after claim, and a coordination/fan-out task never
    gets one (it does no git of its own). Gating the "missing branch_name"
    readiness/stuck condition on this predicate stops the orchestrator from
    auto-blocking a never-claimed PENDING code task that simply hasn't reached
    the claim transition yet (a pending task sat 13min, auto-blocked
    every 30s, never dispatched).
    """
    if _is_coordination_task(task):
        return False
    return str(task.get("status") or "") in _BRANCH_EXPECTED_STATES


def _resolve_agent_cli_model(provider_type: str, model: str) -> str:
    """Translate an agent model name to the string Claude Code expects.

    For the Anthropic provider, short names (``opus|sonnet|haiku``) are
    translated through ``MODEL_MAP`` as they always were.  For non-Anthropic
    providers (currently Ollama Cloud) the model identifier is passed verbatim
    so raw tags like ``kimi-k2.6:cloud`` reach the Ollama-side integration
    intact.

    Extracted as a module-level function so both the ``--model`` CLI arg
    builder and the ``CLAUDE_CODE_SUBAGENT_MODEL`` env-var injector can call
    the same logic without referencing the class by name inside a staticmethod.
    """
    if provider_type == "anthropic":
        return MODEL_MAP.get(model, model)
    return model


def _agent_workspace_path(project_slug: str, team: str, agent_id: str) -> str:
    """Per-agent workspace path inside the container.

    Mirrors the bind-mount layout: the host's workspaces dir is mounted at
    /data/workspaces (orchestrator.py mount args), so each agent's clone lives
    at /data/workspaces/<project>/<team>/<agent>. Used by both
    _get_role_permissions (Edit/Write allowlist) and _build_mount_args
    (docker ``-w`` flag) so the cwd matches the allowlist scope.
    """
    return f"/data/workspaces/{project_slug}/{team}/{agent_id}"


def _cell_workspace_path(project_slug: str, team: str) -> str:
    """Cell-level workspace path (documenter scope).

    Same rationale as ``_agent_workspace_path``; documenters work at the cell
    branch, not a per-agent dev branch.
    """
    return f"/data/workspaces/{project_slug}/{team}"


def _resolve_project_slug_from_git_context(
    git_context: SpawnGitContext | None,
) -> str:
    """Extract project_slug from git_context, falling back to 'default'.

    Module-level counterpart to the instance method ``_resolve_project_slug``.
    Called by static / classmethod contexts (e.g. ``_build_mount_args``) that
    cannot access ``self``. The fallback warning is omitted here because the
    instance method already logs it when the full spawn path runs; this helper
    is only for the mount-args path where the agent_id/task_id context is not
    available.
    """
    if git_context and git_context.project_slug:
        return git_context.project_slug
    return "default"


# Phase 4: every role gets a gateway manifest. The legacy briefing path is gone.
GATEWAY_ENABLED_ROLES: frozenset[str] = frozenset(
    {
        "developer",
        "qa",
        "documenter",
        "cell_pm",
        "main_pm",
        "product_owner",
        "head_marketing",
        "auditor",
    }
)


def _build_manifest_for_agent(agent_id: str, model: str) -> Path | None:
    """Write a SpawnManifest for developer-role agents; return the host path.

    Returns ``None`` for roles outside ``GATEWAY_ENABLED_ROLES`` so callers
    can skip the manifest mount entirely without extra branching.

    Args:
        agent_id: Agent slug (e.g. ``be-dev-1``).
        model:    Resolved model name passed to ``SpawnInputs.agent_model``.

    Returns:
        Absolute host path to the written JSON file, or ``None``.
    """
    from uuid import UUID

    from roboco.runtime.spawn_manifest import (
        SpawnInputs,
        build_for_role,
        write_manifest,
    )

    role = get_agent_role(agent_id) or "developer"
    if role not in GATEWAY_ENABLED_ROLES:
        return None

    team = get_agent_team(agent_id) or "backend"
    # UUID for the agent comes from the seeded AGENT_UUIDS map (slug -> UUID
    # string).  Fall back to uuid4 for unknown agents so the function stays
    # callable in tests without seeded data.
    raw_uuid = AGENT_UUIDS.get(agent_id)
    agent_uuid = UUID(raw_uuid) if raw_uuid else __import__("uuid").uuid4()

    workspace_path = Path(settings.workspaces_root) / "roboco" / team / agent_id

    manifest = build_for_role(
        SpawnInputs(
            agent_id=agent_uuid,
            role=role,
            team=team,
            workspace_path=workspace_path,
            agent_model=model,
        )
    )

    # Two paths in play:
    #   - orchestrator-internal: where the file is written inside the
    #     orchestrator container (settings.manifest_host_dir). The compose
    #     volume mount makes this dir visible on the host.
    #   - host-side: what the docker daemon needs for the bind-mount into
    #     the spawned agent. Computed via DATA_HOST_PATH translation.
    write_dir = Path(settings.manifest_host_dir)
    write_path = write_dir / f"{agent_id}.json"
    write_manifest(manifest, write_path)
    if DATA_HOST_PATH:
        return Path(f"{DATA_HOST_PATH}/manifests/{agent_id}.json")
    return write_path
