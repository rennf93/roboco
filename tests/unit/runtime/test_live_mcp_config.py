"""Live MCP config must launch the real server modules, keyed by server name."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from roboco.runtime.engines.interactive_sessions import InteractiveSessionsEngine


@pytest.mark.parametrize(
    ("role", "name", "module"),
    [
        ("secretary", "roboco-secretary", "roboco.mcp.secretary_server"),
        ("intake", "roboco-intake", "roboco.mcp.intake_server"),
    ],
)
def test_live_mcp_config_module_names(role: str, name: str, module: str) -> None:
    path = InteractiveSessionsEngine._write_live_mcp_config(role, "sid", "http://x")
    server = json.loads(Path(path).read_text())["mcpServers"][name]
    assert server["args"][-1] == module
