"""Tests for roboco.mcp.utils._get_agent_headers UNSIGNED-token guard."""

from __future__ import annotations


def test_get_agent_headers_omits_unsigned_token(monkeypatch):
    from roboco.mcp import utils as mcp_utils

    monkeypatch.setenv("ROBOCO_AGENT_TOKEN", "UNSIGNED")
    h = mcp_utils._get_agent_headers("be-dev-1")
    assert "X-Agent-Token" not in h
    assert h["X-Agent-ID"] == "be-dev-1"


def test_get_agent_headers_sends_real_token(monkeypatch):
    from roboco.mcp import utils as mcp_utils

    monkeypatch.setenv("ROBOCO_AGENT_TOKEN", "signed-token-abc")
    h = mcp_utils._get_agent_headers("be-dev-1")
    assert h["X-Agent-Token"] == "signed-token-abc"
