"""Compose contract: every service that spawns agents binds the agent plugin dir.

Agent containers bind ``<data>/agent-claude-plugins`` over
``~/.claude/plugins``. The orchestrator creates that dir at
``AGENT_PLUGINS_DATA_DIR`` before the run, and the docker-gate refuses a bind
source missing on disk, so the container path must be the host dir. Live
2026-11-02: green had no such bind, the dir was made inside the container
only, and the gate refused every agent spawn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from roboco.runtime.orchestrator import AGENT_PLUGINS_DATA_DIR

_REPO_ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    "compose_file",
    ["docker-compose.yml", "docker-compose.yaml", "docker-compose.registry.yml"],
)
def test_every_agent_spawner_binds_the_agent_plugin_dir(compose_file: str) -> None:
    compose: dict[str, Any] = yaml.safe_load((_REPO_ROOT / compose_file).read_text())
    volumes_of = {
        name: [str(v) for v in service.get("volumes") or []]
        for name, service in compose["services"].items()
    }
    spawners = {
        name: volumes
        for name, volumes in volumes_of.items()
        if any(v.endswith(":/data/workspaces") for v in volumes)
    }
    assert spawners
    for name, volumes in spawners.items():
        binds = [v for v in volumes if v.endswith(f":{AGENT_PLUGINS_DATA_DIR}")]
        assert binds, f"{compose_file}: {name} lacks the agent plugin dir bind"
        assert binds[0].split(":/data/")[0].endswith("/agent-claude-plugins")
