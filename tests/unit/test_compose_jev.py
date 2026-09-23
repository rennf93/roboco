"""Compose contract for the roboco-jev sidecar (the Laya tier of the
Decisions service, docs/internal/jev-decisions-spec.md sections 8 Stage 0.5
and 10).

The sidecar is an inert-until-armed HTTP service (Laya typed decisions over
the official onnxruntime path, mirroring the OpenRouter Decisions wire
shape). Its compose contract is deliberately narrow:

- it exists in BOTH compose files (build + registry), byte-consistent with
  how the video-renderer sidecar landed;
- it declares NO `networks:` key: it joins the implicit default network
  (roboco_default) next to ollama by construction. Giving it a networks key
  risks homing it on roboco_data (the DB-only network) - a decisions
  sidecar has no business there;
- it has a healthcheck (the resolver's cached probe depends on /health);
- it is NOT exposed on host ports (internal bridge only, never through
  nginx either);
- it carries a memory cap (int8 ONNX weights + onnxruntime fit in 1-2 GB;
  the cap is the guardrail);
- the resolver URL env plumbing points at the container name on port 8100.

The docker-compose.yml/.yaml byte-identity gate (make compose-sync) is
re-pinned here too, like the Makefile gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).parents[2]
_BUILD_COMPOSE = "docker-compose.yml"
_REGISTRY_COMPOSE = "docker-compose.registry.yml"
_COMPOSE_FILES = (_BUILD_COMPOSE, _REGISTRY_COMPOSE)

_JEV_CONTAINER = "roboco-jev"
_DECISIONS_ENV_KEYS = (
    "ROBOCO_DECISIONS_ENABLED",
    "ROBOCO_DECISIONS_BASE_URL",
    "ROBOCO_DECISIONS_TIER_LAYA_ENABLED",
    "ROBOCO_DECISIONS_TIER_OPENROUTER_ENABLED",
)


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((_REPO_ROOT / name).read_text())


def _jev_service(compose: dict[str, Any], name: str) -> dict[str, Any]:
    """Find the jev service by container_name (the service key may differ
    from the container name, like video-renderer/roboco-video-renderer)."""
    for service in compose["services"].values():
        if service.get("container_name") == _JEV_CONTAINER:
            return service
    raise AssertionError(f"{name}: no service with container_name {_JEV_CONTAINER}")


def test_jev_exists_in_both_composes_with_expected_posture() -> None:
    for name in _COMPOSE_FILES:
        compose = _load(name)
        jev = _jev_service(compose, name)
        # Default-bridge posture: NO networks key at all. The compose
        # default network IS roboco_default (named below), so omitting the
        # key is what puts the sidecar next to ollama; an explicit key
        # would be a drift vector toward roboco_data.
        assert "networks" not in jev, (
            f"{name}: roboco-jev must not declare a networks key - it joins "
            "the implicit default (roboco_default) next to ollama; a "
            "networks key risks homing the decisions sidecar on roboco_data"
        )
        assert "healthcheck" in jev, (
            f"{name}: roboco-jev must carry a healthcheck - the decisions "
            "resolver's cached probe depends on /health"
        )
        assert jev.get("mem_limit"), f"{name}: roboco-jev must carry a mem_limit cap"
        assert "ports" not in jev or not jev["ports"], (
            f"{name}: roboco-jev must not publish host ports (internal bridge only)"
        )
        assert jev.get("restart") == "unless-stopped", (
            f"{name}: roboco-jev must restart unless-stopped like every sidecar"
        )


def test_jev_not_exposed_on_host_ports_in_build_compose() -> None:
    # Explicit re-pin of the "not on the host" rule for the build compose
    # (covered for both files above; this one names the file in the failure).
    compose = _load(_BUILD_COMPOSE)
    jev = _jev_service(compose, _BUILD_COMPOSE)
    assert not jev.get("ports"), (
        "docker-compose.yml: roboco-jev must not be exposed on host ports"
    )


def test_jev_resolver_url_points_at_container() -> None:
    """The orchestrator env must resolve decisions to the sidecar's
    container name on 8100 - a localhost/loopback default would die inside
    the orchestrator container exactly like the 2026-09-06 dispatcher
    defect (see test_compose_roles.py)."""
    for name in _COMPOSE_FILES:
        compose = _load(name)
        env = compose["x-orchestrator-env"]
        value = str(env.get("ROBOCO_DECISIONS_BASE_URL", ""))
        expected = "${ROBOCO_DECISIONS_BASE_URL:-http://roboco-jev:8100}"
        assert value == expected, (
            f"{name}: ROBOCO_DECISIONS_BASE_URL must default to "
            f"http://roboco-jev:8100, got {value}"
        )
        assert "roboco-jev:8100" in value and not any(
            banned in value for banned in ("127.0.0.1", "localhost", "0.0.0.0")
        ), f"{name}: ROBOCO_DECISIONS_BASE_URL must name the sidecar container"
        assert env.get("ROBOCO_DECISIONS_ENABLED") == (
            "${ROBOCO_DECISIONS_ENABLED:-false}"
        ), (
            f"{name}: ROBOCO_DECISIONS_ENABLED must default OFF - shipping the "
            "sidecar changes nothing by itself"
        )
        assert env.get("ROBOCO_DECISIONS_TIER_OPENROUTER_ENABLED") == (
            "${ROBOCO_DECISIONS_TIER_OPENROUTER_ENABLED:-false}"
        ), f"{name}: the OpenRouter fallback tier is OPT-IN and must default OFF"


def test_jev_registry_image_form_matches_sidecar_convention() -> None:
    compose = _load(_REGISTRY_COMPOSE)
    jev = _jev_service(compose, _REGISTRY_COMPOSE)
    expected_image = (
        "${ROBOCO_REGISTRY:-ghcr.io/rennf93}/roboco-jev:${ROBOCO_VERSION:-latest}"
    )
    assert jev.get("image") == expected_image, (
        "docker-compose.registry.yml: roboco-jev must pull from "
        "ROBOCO_REGISTRY/ROBOCO_VERSION like every other sidecar"
    )
    assert "build" not in jev, (
        "docker-compose.registry.yml: the registry compose never builds from source"
    )


def test_compose_yaml_twin_byte_identical() -> None:
    """The blue-green twin must stay byte-identical (make compose-sync)."""
    yml = (_REPO_ROOT / _BUILD_COMPOSE).read_bytes()
    yaml_twin = (_REPO_ROOT / _BUILD_COMPOSE.replace(".yml", ".yaml")).read_bytes()
    assert yml == yaml_twin, (
        "docker-compose.yaml has drifted from docker-compose.yml - copy .yml over .yaml"
    )
