"""Interactive grok entrypoints render the load-bearing MCP wiring into config.toml.

``_render_grok_config`` is the only synchronous, testable part of the interactive
mains (``main()`` needs the live container). It must produce the exact MCP
invocation the branch depends on — ``uv run --directory /app --no-sync`` (the
ModuleNotFound guard), ``UV_PROJECT_ENVIRONMENT=/app/.venv``, and (secretary) the
HMAC identity env the directive tools authenticate with.
"""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

from roboco.agent_sdk import grok_intake_main, grok_secretary_main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_intake_render_wires_roboco_intake_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / ".grok" / "config.toml"
    monkeypatch.setattr(grok_intake_main, "GROK_CONFIG_PATH", cfg)
    grok_intake_main._render_grok_config("http://orch:8000", "sess-1")
    parsed = tomllib.loads(cfg.read_text())
    server = parsed["mcp_servers"]["roboco-intake"]
    assert server["command"] == "uv"
    # The ModuleNotFound guard: --directory /app + --no-sync, installed module.
    assert server["args"] == [
        "run",
        "--directory",
        "/app",
        "--no-sync",
        "python",
        "-m",
        "roboco.mcp.intake_server",
    ]
    assert server["env"]["UV_PROJECT_ENVIRONMENT"] == "/app/.venv"
    assert server["env"]["ROBOCO_API_URL"] == "http://orch:8000"
    assert server["env"]["ROBOCO_PROMPTER_SESSION_ID"] == "sess-1"


def test_secretary_render_wires_mcp_and_hmac_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / ".grok" / "config.toml"
    monkeypatch.setattr(grok_secretary_main, "GROK_CONFIG_PATH", cfg)
    monkeypatch.setenv("ROBOCO_AGENT_ID", "uuid-sec")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "secretary")
    monkeypatch.setenv("ROBOCO_AGENT_TOKEN", "hmac-xyz")
    grok_secretary_main._render_grok_config("http://orch:8000")
    parsed = tomllib.loads(cfg.read_text())
    server = parsed["mcp_servers"]["roboco-secretary"]
    assert server["args"] == [
        "run",
        "--directory",
        "/app",
        "--no-sync",
        "python",
        "-m",
        "roboco.mcp.secretary_server",
    ]
    # The HMAC identity the directive tools authenticate with must flow through.
    assert server["env"]["ROBOCO_AGENT_TOKEN"] == "hmac-xyz"
    assert server["env"]["ROBOCO_AGENT_ID"] == "uuid-sec"
    assert server["env"]["ROBOCO_AGENT_ROLE"] == "secretary"
    assert server["env"]["UV_PROJECT_ENVIRONMENT"] == "/app/.venv"
