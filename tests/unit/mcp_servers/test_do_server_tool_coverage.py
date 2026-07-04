"""Every do-verb granted to a role must be registered in ``do_server._TOOLS``.

Regression guard for the class of bug where a new content verb is wired at the
role-config + content-actions + route layers but never added to do_server's
``_TOOLS`` registry — ``_register_tools()`` then registers only the intersection
of granted verbs and ``_TOOLS``, silently dropping the verb, so the agent can
never call it. This is exactly what happened to ``propose_feature_spotlight`` in
the v0.18.0 feature-spotlight work.
"""

import importlib

import pytest
from roboco.services.gateway.role_config import ROLE_CONFIGS


def test_every_granted_do_tool_is_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    # do_server reads agent/orchestrator env + a manifest at import; the
    # ROBOCO_ALLOW_FULL_TOOLSET escape hatch lets it register the full _TOOLS
    # set without a manifest file.
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000099")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_SDK_URL", "http://test-sdk:9000")
    monkeypatch.setenv("ROBOCO_ALLOW_FULL_TOOLSET", "1")

    from roboco.mcp import do_server

    importlib.reload(do_server)

    registered = set(do_server._TOOLS)
    missing = sorted(
        (cfg.role, verb)
        for cfg in ROLE_CONFIGS.values()
        for verb in cfg.do_tools
        if verb not in registered
    )
    assert not missing, (
        "do-tools granted in role_config but absent from do_server._TOOLS "
        f"(the MCP server cannot expose them): {missing}"
    )
