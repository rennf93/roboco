"""The SDK server's direct orchestrator calls must carry the agent HMAC
token + team, or the API's ``ROBOCO_AGENT_AUTH_REQUIRED`` gate 401s with
"Missing X-Agent-Token" — regression: the session-end post-mortem flush
(``/api/journals/me/entries``), A2A persistence/fallback, and
auto-substitute call all built the header dict by hand and omitted both,
latent until auth was armed on the NAS deploy.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import roboco.agent_sdk.server as srv

if TYPE_CHECKING:
    import pytest


def test_agent_headers_carries_token_and_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    be_dev_1 = "00000000-0000-0000-0001-000000000001"  # role=developer, team=backend
    monkeypatch.setenv("ROBOCO_AGENT_ID", be_dev_1)
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_AGENT_TOKEN", "test-hmac-token")
    importlib.reload(srv)

    headers = srv._agent_headers()

    assert headers["X-Agent-ID"] == be_dev_1
    assert headers["X-Agent-Role"] == "developer"
    assert headers["X-Agent-Team"] == "backend"
    assert headers["X-Agent-Token"] == "test-hmac-token"


def test_agent_headers_omits_team_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A team-less agent (the `system` sentinel) confirms the team header is
    # omitted, not sent empty — so the middleware passes "" and matches a
    # token signed with team="".
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "system")
    monkeypatch.setenv("ROBOCO_AGENT_TOKEN", "test-hmac-token")
    importlib.reload(srv)

    headers = srv._agent_headers()

    assert "X-Agent-Team" not in headers
    assert headers["X-Agent-Token"] == "test-hmac-token"
