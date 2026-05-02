"""Per-role spawn manifest builder.

Composes the role config (allowed verbs + content tools) with per-agent
context (id, team, workspace, model) into a JSON manifest the SDK shim
reads at startup. Eliminates ToolSearch — tools are pre-registered.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from roboco.services.gateway.role_config import get_role_config

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

_READ_TOOLS = ["Read", "Glob", "Grep"]
_WRITE_TOOLS = ["Edit", "Write"]


@dataclass
class SpawnInputs:
    """Caller-supplied inputs for building a spawn manifest."""

    agent_id: UUID
    role: str
    team: str
    workspace_path: Path
    agent_model: str
    extra_env: dict[str, str] | None = None


@dataclass
class SpawnManifest:
    """Tool manifest written to /app/tool-manifest.json inside agent containers."""

    agent_id: str
    role: str
    team: str
    workspace_path: str
    flow_tools: list[str]
    do_tools: list[str]
    read_tools: list[str]
    write_tools: list[str]
    bash_allowed: bool
    subagent_allowed: bool
    subagent_model: str | None
    env: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_for_role(inputs: SpawnInputs) -> SpawnManifest:
    """Construct a SpawnManifest for the given role + agent inputs."""
    cfg = get_role_config(inputs.role)
    return SpawnManifest(
        agent_id=str(inputs.agent_id),
        role=inputs.role,
        team=inputs.team,
        workspace_path=str(inputs.workspace_path),
        flow_tools=list(cfg.flow_tools),
        do_tools=list(cfg.do_tools),
        read_tools=list(_READ_TOOLS),
        write_tools=list(_WRITE_TOOLS) if cfg.allows_write else [],
        bash_allowed=True,  # always; bash-guard hook still applies server-side
        subagent_allowed=cfg.allows_subagent,
        subagent_model=inputs.agent_model if cfg.allows_subagent else None,
        env={
            "ROBOCO_AGENT_ID": str(inputs.agent_id),
            "ROBOCO_AGENT_ROLE": inputs.role,
            "ROBOCO_AGENT_TEAM": inputs.team,
            "ROBOCO_PUBLIC_BASE_URL": "http://127.0.0.1:8000",
            **(inputs.extra_env or {}),
        },
    )


def write_manifest(manifest: SpawnManifest, path: Path) -> None:
    """Serialize a SpawnManifest to JSON at the given path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=True))
