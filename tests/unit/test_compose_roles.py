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


def test_dispatcher_api_url_never_loopback_and_color_correct() -> None:
    """2026-09-06 defect one, re-pinned 2026-09-17: setting ROBOCO_API_URL on
    the dispatcher is now REQUIRED (blue-green) but has two hard invariants.
    It must never be a loopback address (the original incident: loopback is
    unreachable from spawned agent containers, so every gateway verb in
    every agent died with httpx.ConnectError) and, in the blue-green
    compose, it must name the service's OWN color's dispatcher (a spawned
    container resolves its generation over the shared docker network; the
    old non-color name died with the rename). The registry compose keeps
    the singular name."""
    own_dispatcher = {
        "docker-compose.yml": {
            "dispatcher-blue": "http://roboco-dispatcher-blue:8000",
            "dispatcher-green": "http://roboco-dispatcher-green:8000",
        },
        "docker-compose.registry.yml": {
            "dispatcher": "http://roboco-orchestrator:8000",
        },
    }
    for name in _COMPOSE_FILES:
        compose = yaml.safe_load((_REPO_ROOT / name).read_text())
        dispatchers = _role_services(compose, "dispatcher")
        assert dispatchers, f"{name}: no dispatcher service found"
        expected_map = own_dispatcher[name]
        for service_name in expected_map:
            service = compose["services"][service_name]
            env = service.get("environment") or {}
            value = str(env.get("ROBOCO_API_URL", ""))
            assert value, (
                f"{name}/{service_name}: ROBOCO_API_URL must be set - spawned "
                "containers resolve their own generation's dispatcher through it"
            )
            for banned in ("127.0.0.1", "localhost", "0.0.0.0"):
                assert banned not in value, (
                    f"{name}/{service_name}: loopback ROBOCO_API_URL ({value}) - "
                    "_generate_mcp_config hands it to every spawned agent's "
                    "MCP config and agents cannot reach the dispatcher's loopback"
                )
            expected = expected_map[service_name]
            assert value == expected, (
                f"{name}/{service_name}: ROBOCO_API_URL must be {expected}, got {value}"
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


def test_nginx_mounts_front_include_dir_in_every_compose() -> None:
    """#1111: deploy/nginx.conf includes /etc/nginx/front/active-upstreams.conf
    (the blue-green color switch), so EVERY compose file must mount the repo's
    ./front directory at /etc/nginx/front. 2026-09-21 the registry compose
    shipped without it and fresh `make quickstart` crash-looped nginx on the
    missing include. The mount must be the DIRECTORY, not a file bind: a file
    bind pins the inode and the deploy script's atomic tmp+mv swap would never
    reach the running container."""
    for name in _COMPOSE_FILES:
        compose = yaml.safe_load((_REPO_ROOT / name).read_text())
        nginx = compose["services"].get("nginx")
        assert nginx is not None, f"{name}: no nginx service found"
        volumes = [str(v) for v in (nginx.get("volumes") or [])]
        dir_mount = any(
            v.startswith("./front:") and v.endswith(":/etc/nginx/front:ro")
            for v in volumes
        )
        assert dir_mount, (
            f"{name}: nginx must mount ./front:/etc/nginx/front:ro (directory "
            "bind, not a file bind) or the active-upstreams include in "
            "deploy/nginx.conf crash-loops the container on a fresh install"
        )
        # And the included file must actually exist in the repo, so the mount
        # is never empty on a clean clone.
        assert (_REPO_ROOT / "front" / "active-upstreams.conf").is_file(), (
            "front/active-upstreams.conf is missing from the repo; the nginx "
            "include would 404 it and crash-loop roboco-nginx"
        )
