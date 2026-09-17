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

2026-09-17: the single-file blue-green compose (docker-compose.yml/.yaml)
renamed the role services to per-color pairs (dispatcher-blue/green,
indexer-blue/green); the registry compose keeps the singular names. The
contract below is ROLE-scoped, not service-name-scoped, so both layouts are
covered and a future rename cannot silently zero the check.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).parents[2]
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.registry.yml")


def _role_services(compose: dict[str, Any], role: str) -> list[dict[str, Any]]:
    """Every service definition carrying *role* in its name, singular or
    color-suffixed (``indexer``, ``dispatcher-blue``, ...)."""
    return [
        service
        for name, service in compose["services"].items()
        if name == role or name.startswith(f"{role}-")
    ]


def test_dispatcher_services_never_set_a_loopback_api_url() -> None:
    for name in _COMPOSE_FILES:
        compose = yaml.safe_load((_REPO_ROOT / name).read_text())
        dispatchers = _role_services(compose, "dispatcher")
        assert dispatchers, f"{name}: no dispatcher service found"
        for service in dispatchers:
            env = service.get("environment") or {}
            assert "ROBOCO_API_URL" not in env, (
                f"{name}: a dispatcher service must not set ROBOCO_API_URL - "
                "_generate_mcp_config hands it to every spawned agent's MCP config"
            )


def test_indexer_services_have_no_http_healthcheck() -> None:
    """The indexer role never runs uvicorn (roboco/bootstrap.py's
    _run_indexer_role only awaits run_indexer), so an HTTP probe can never
    pass - 2026-09-06 it sat `unhealthy` on the NAS for two hours."""
    for name in _COMPOSE_FILES:
        compose = yaml.safe_load((_REPO_ROOT / name).read_text())
        indexers = _role_services(compose, "indexer")
        assert indexers, f"{name}: no indexer service found"
        for service in indexers:
            assert "healthcheck" not in service, (
                f"{name}: an indexer service serves no HTTP, a healthcheck "
                "can never pass"
            )
