"""Startup self-heal: kill agent containers whose baked-in ROBOCO_AGENT_TOKEN
no longer verifies against the current ``ROBOCO_AGENT_AUTH_SECRET``.

A token is signed once at spawn. If the secret drifts afterwards (a `.env`
change, a compose recreate that reloads the orchestrator's env without
recreating the agent containers), the surviving agent keeps sending its stale
token and the middleware 401s every verb with "signature mismatch". The
container stays alive (heartbeating) so the reaper never reclaims it and no
fresh agent spawns — the fleet stalls. ``_heal_stale_agent_tokens`` runs at
startup and kills each stale-token container so normal dispatch re-spawns it
with a freshly signed token.
"""

from __future__ import annotations

import secrets
from typing import Any
from unittest.mock import AsyncMock

import pytest
from roboco.agents_config import (
    AGENT_UUIDS,
    issue_agent_token,
    verify_agent_token,
)
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> Any:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)  # bypass __init__
    orch._instances = {}
    return orch


@pytest.mark.asyncio
async def test_heal_kills_stale_token_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = secrets.token_hex(32)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", secret)
    orch = _orch()
    # be-dev-1 holds a token signed with a DIFFERENT secret (rotated after spawn).
    stale_token = issue_agent_token(
        "00000000-0000-0000-0001-000000000001", "developer", "backend"
    )
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", secrets.token_hex(32))

    async def inspect(name: str) -> tuple[bool, int | None]:
        return (name == "roboco-agent-be-dev-1", 0)

    orch._inspect_container_state = AsyncMock(side_effect=inspect)
    orch._read_container_auth_env = AsyncMock(
        return_value=(
            stale_token,
            "00000000-0000-0000-0001-000000000001",
            "developer",
        )
    )
    removed: list[str] = []
    orch._remove_container = AsyncMock(
        side_effect=lambda name, **_: removed.append(name)
    )

    n = await orch._heal_stale_agent_tokens()

    assert n == 1
    assert removed == ["roboco-agent-be-dev-1"]


@pytest.mark.asyncio
async def test_heal_leaves_valid_token_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", secrets.token_hex(32))
    orch = _orch()
    be_dev_1 = "00000000-0000-0000-0001-000000000001"
    valid_token = issue_agent_token(be_dev_1, "developer", "backend")

    async def inspect(name: str) -> tuple[bool, int | None]:
        return (name == "roboco-agent-be-dev-1", 0)

    orch._inspect_container_state = AsyncMock(side_effect=inspect)
    orch._read_container_auth_env = AsyncMock(
        return_value=(valid_token, be_dev_1, "developer")
    )
    orch._remove_container = AsyncMock()

    n = await orch._heal_stale_agent_tokens()

    assert n == 0
    orch._remove_container.assert_not_called()


@pytest.mark.asyncio
async def test_heal_inert_when_secret_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Dev mode (no secret): verify_agent_token fails for every token, so an
    # ungated heal would kill the whole fleet. The heal must short-circuit.
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_SECRET", raising=False)
    orch = _orch()
    orch._inspect_container_state = AsyncMock(return_value=(True, 0))
    orch._read_container_auth_env = AsyncMock(
        return_value=("UNSIGNED", "be-dev-1", "developer")
    )
    orch._remove_container = AsyncMock()

    n = await orch._heal_stale_agent_tokens()

    assert n == 0
    orch._remove_container.assert_not_called()


@pytest.mark.asyncio
async def test_heal_skips_when_env_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", secrets.token_hex(32))
    orch = _orch()
    orch._inspect_container_state = AsyncMock(return_value=(True, 0))
    # docker exec fails (container mid-shutdown, etc.) → None → skip, don't kill.
    orch._read_container_auth_env = AsyncMock(return_value=None)
    orch._remove_container = AsyncMock()

    n = await orch._heal_stale_agent_tokens()

    assert n == 0
    orch._remove_container.assert_not_called()


@pytest.mark.asyncio
async def test_heal_kills_slug_env_container_with_slug_signed_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-fix container carries a slug ROBOCO_AGENT_ID + a slug-signed token.

    The MCP servers send X-Agent-ID as the UUID (gateway v1 parses it as
    Annotated[UUID]), so the middleware verifies the token against the UUID
    — a slug-signed token 401s. The heal must verify against the UUID too:
    a slug-signed token is stale wrt the UUID identity and the container must
    be killed so it respawns with a UUID-signed token. Verifying against the
    container-env slug would PASS and leave the stale container 401ing.
    """
    secret = secrets.token_hex(32)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", secret)
    orch = _orch()
    be_dev_1_slug = "be-dev-1"
    # Token signed over the slug (the pre-fix _append_agent_auth_env behaviour).
    slug_signed_token = issue_agent_token(be_dev_1_slug, "developer", "backend")

    async def inspect(name: str) -> tuple[bool, int | None]:
        return (name == "roboco-agent-be-dev-1", 0)

    orch._inspect_container_state = AsyncMock(side_effect=inspect)
    orch._read_container_auth_env = AsyncMock(
        return_value=(slug_signed_token, be_dev_1_slug, "developer")
    )
    removed: list[str] = []
    orch._remove_container = AsyncMock(
        side_effect=lambda name, **_: removed.append(name)
    )

    n = await orch._heal_stale_agent_tokens()

    assert n == 1
    assert removed == ["roboco-agent-be-dev-1"]


def test_heal_accepts_expiring_format_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heal verifier must accept the new {payload}.{sig} expiring token.

    Task 1 made verify_agent_token format-agnostic; this pins it at the heal
    call site so a post-deploy respawn with a ttl token isn't killed.
    """
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "heal-secret")
    uuid = AGENT_UUIDS.get("be-dev-1", "be-dev-1")
    tok = issue_agent_token(uuid, "developer", "backend", ttl_seconds=3600)
    assert verify_agent_token(tok, uuid, "developer", "backend") is True
