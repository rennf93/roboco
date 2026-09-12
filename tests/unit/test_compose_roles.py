"""Compose contract for the split roles (dispatcher / indexer).

2026-09-06, defect one: a loopback ROBOCO_API_URL pinned on the dispatcher
(so its own internal_api_url would resolve to its own uvicorn) was handed
straight through to every spawned agent container's MCP config by
`_generate_mcp_config`, which reads `settings.api_url` directly - every
gateway verb in every agent container crashed with httpx.ConnectError.
internal_api_url now derives the dispatcher's loopback address from its role
in code (roboco/config.py), so the env var is never needed there again.

2026-09-06, defect two: the indexer role never runs uvicorn
(roboco/bootstrap.py's _run_indexer_role only awaits run_indexer), so an
HTTP healthcheck added to the indexer service can never pass - it sat
`unhealthy` on the NAS for two hours while doing normal work.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parents[2]
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.registry.yml")


def test_dispatcher_never_sets_a_loopback_api_url() -> None:
    for name in _COMPOSE_FILES:
        compose = yaml.safe_load((_REPO_ROOT / name).read_text())
        env = compose["services"]["dispatcher"]["environment"]
        assert "ROBOCO_API_URL" not in env, (
            f"{name}: dispatcher must not set ROBOCO_API_URL - "
            "_generate_mcp_config hands it to every spawned agent's MCP config"
        )


def test_indexer_has_no_http_healthcheck() -> None:
    """The indexer role never runs uvicorn (roboco/bootstrap.py's
    _run_indexer_role only awaits run_indexer), so an HTTP probe can never
    pass - 2026-09-06 it sat `unhealthy` on the NAS for two hours."""
    for name in _COMPOSE_FILES:
        compose = yaml.safe_load((_REPO_ROOT / name).read_text())
        assert "healthcheck" not in compose["services"]["indexer"], (
            f"{name}: indexer serves no HTTP, a healthcheck can never pass"
        )
