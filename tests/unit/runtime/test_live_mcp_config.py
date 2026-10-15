"""Live MCP config must launch the real server modules, keyed by server name."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from roboco.runtime.engines import interactive_sessions
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


def test_live_mcp_config_written_to_mount_returns_host_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Containerized: the file lands in the container side of the mcp-configs
    mount and the -v source is the host side of it. Writing the host path from
    inside the container never reaches the host, and the daemon then mounts an
    empty directory over /app/mcp-config.json (the intake gets no tools)."""
    host_data = tmp_path / "host-data"
    monkeypatch.setattr(interactive_sessions, "PROJECT_HOST_PATH", str(tmp_path))
    monkeypatch.setattr(interactive_sessions, "DATA_HOST_PATH", str(host_data))
    monkeypatch.setattr(interactive_sessions, "_LIVE_MCP_WRITE_DIR", tmp_path / "mount")

    path = InteractiveSessionsEngine._write_live_mcp_config("intake", "sid", "http://x")

    assert path == f"{host_data}/mcp-configs/live-intake-sid.json"
    written = tmp_path / "mount" / "live-intake-sid.json"
    assert "roboco-intake" in json.loads(written.read_text())["mcpServers"]
    assert not (host_data / "mcp-configs").exists()
