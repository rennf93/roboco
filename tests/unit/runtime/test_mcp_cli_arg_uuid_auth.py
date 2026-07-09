"""roboco-optimal/docs/search receive the agent's CLI arg as sys.argv[1] and
forward it verbatim as X-Agent-ID via ApiClient/_get_agent_headers. The spawn
token (_append_agent_auth_env) is signed over the agent's UUID, so that CLI
arg must be the UUID too, or verify_agent_token 401s with a signature
mismatch even though role/team resolve fine either way (get_agent_role/
get_agent_team accept slug or UUID via _resolve_to_slug).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from roboco.agents_config import AGENT_UUIDS, verify_agent_token
from roboco.config import settings
from roboco.mcp import utils as mcp_utils
from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    import pytest

# main-pm carries roboco-optimal (always), roboco-docs (docs_roles) and
# roboco-search (research_roles, research_enabled defaults True) all at once.
_AGENT_SLUG = "main-pm"
_CLI_ARG_SERVERS = ("roboco-optimal", "roboco-docs", "roboco-search")


def _spawn_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Mint the token exactly as _append_agent_auth_env does at spawn."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "spawn-secret")
    monkeypatch.setattr(settings, "agent_token_ttl_seconds", 3600)
    cmd: list[str] = []
    config = AgentConfig(
        agent_id=_AGENT_SLUG,
        blueprint_path=Path("/app/blueprints/main-pm.md"),
        provider_type="anthropic",
    )
    AgentOrchestrator._append_agent_auth_env(cmd, config)
    for i, flag in enumerate(cmd):
        if flag == "-e" and cmd[i + 1].startswith("ROBOCO_AGENT_TOKEN="):
            return cmd[i + 1].split("=", 1)[1]
    raise AssertionError("ROBOCO_AGENT_TOKEN not found in cmd")


async def test_cli_arg_servers_get_uuid_not_slug() -> None:
    """_generate_mcp_config passes the UUID (not the slug) as sys.argv[1]
    to the three servers that identify their agent via CLI arg."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    config_path = await orch._generate_mcp_config(_AGENT_SLUG)
    config = json.loads(Path(config_path).read_text())
    servers = config["mcpServers"]
    expected_uuid = AGENT_UUIDS[_AGENT_SLUG]
    for name in _CLI_ARG_SERVERS:
        assert name in servers, f"{name} should be mounted for {_AGENT_SLUG}"
        cli_arg = servers[name]["args"][-1]
        assert cli_arg == expected_uuid, (
            f"{name} sys.argv[1] is {cli_arg!r}, expected the UUID "
            f"{expected_uuid!r} — a slug here mismatches the UUID-signed "
            f"spawn token and every call 401s."
        )


async def test_cli_arg_servers_headers_verify_against_spawn_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The header tuple _get_agent_headers builds from the CLI arg
    _generate_mcp_config hands these servers must verify against the token
    the orchestrator actually injects into the container env."""
    token = _spawn_token(monkeypatch)
    monkeypatch.setenv("ROBOCO_AGENT_TOKEN", token)

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    config_path = await orch._generate_mcp_config(_AGENT_SLUG)
    config = json.loads(Path(config_path).read_text())
    servers = config["mcpServers"]

    for name in _CLI_ARG_SERVERS:
        cli_arg = servers[name]["args"][-1]
        headers = mcp_utils._get_agent_headers(cli_arg)
        assert verify_agent_token(
            headers["X-Agent-Token"],
            headers["X-Agent-ID"],
            headers["X-Agent-Role"],
            headers.get("X-Agent-Team", ""),
        ), f"{name}'s header tuple ({headers}) failed verify_agent_token"
