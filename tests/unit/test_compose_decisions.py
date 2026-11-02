"""Compose contract for the roboco-decisions sidecar (the self-hosted tiers
of the Decisions service, docs/internal/decisions-spec.md sections 8
Stage 0.5 and 10).

The sidecar is an inert-until-armed HTTP service (Clef-Flash 9B + Laya 421M
typed decisions over llama.cpp backends behind one proxy, mirroring the
OpenRouter Decisions wire shape). Its compose contract is deliberately
narrow:

- it exists in BOTH compose files (build + registry), byte-consistent with
  how the video-renderer sidecar landed;
- it declares NO `networks:` key: it joins the implicit default network
  (roboco_default) next to ollama by construction. Giving it a networks key
  risks homing it on roboco_data (the DB-only network) - a decisions
  sidecar has no business there;
- it has a healthcheck (the resolver's cached probe depends on /health);
- it is NOT exposed on host ports (internal bridge only, never through
  nginx either);
- no memory cap, deliberately (the Clef-Flash GGUF loads into RAM; an
  earlier low cap rendered the container useless and was removed);
- one input-cap knob (ROBOCO_DECISIONS_CLEF_MAX_INPUT_TOKENS) reaches both
  the orchestrator's client and the sidecar's llama.cpp batch;
- docker-compose.igpu.yaml, added by scripts/deploy-nas.sh only when the
  host has /dev/dri/renderD128, puts the sidecar and ollama on the Intel
  iGPU;
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

_SIDECAR_CONTAINER = "roboco-decisions"
_DECISIONS_ENV_KEYS = (
    "ROBOCO_DECISIONS_ENABLED",
    "ROBOCO_DECISIONS_BASE_URL",
    "ROBOCO_DECISIONS_TIER_CLEF_ENABLED",
    "ROBOCO_DECISIONS_TIER_LAYA_ENABLED",
    "ROBOCO_DECISIONS_TIER_OPENROUTER_ENABLED",
    "ROBOCO_DECISIONS_CLEF_MAX_INPUT_TOKENS",
)


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((_REPO_ROOT / name).read_text())
    return data


def _sidecar_service(compose: dict[str, Any], name: str) -> dict[str, Any]:
    """Find the decisions service by container_name (the service key may differ
    from the container name, like video-renderer/roboco-video-renderer)."""
    for service in compose["services"].values():
        svc: dict[str, Any] = service
        if svc.get("container_name") == _SIDECAR_CONTAINER:
            return svc
    raise AssertionError(f"{name}: no service with container_name {_SIDECAR_CONTAINER}")


def test_sidecar_exists_in_both_composes_with_expected_posture() -> None:
    for name in _COMPOSE_FILES:
        compose = _load(name)
        sidecar = _sidecar_service(compose, name)
        # Default-bridge posture: NO networks key at all. The compose
        # default network IS roboco_default (named below), so omitting the
        # key is what puts the sidecar next to ollama; an explicit key
        # would be a drift vector toward roboco_data.
        assert "networks" not in sidecar, (
            f"{name}: roboco-decisions must not declare a networks key - it joins "
            "the implicit default (roboco_default) next to ollama; a "
            "networks key risks homing the decisions sidecar on roboco_data"
        )
        assert "healthcheck" in sidecar, (
            f"{name}: roboco-decisions must carry a healthcheck - the decisions "
            "resolver's cached probe depends on /health"
        )
        # NO mem_limit, deliberately: the original low cap rendered the
        # container useless (2fcca85d removed it) - the decisions resolver's
        # in-process model needs headroom. Do not reintroduce a cap without
        # measuring the resolver's real working set.
        assert "mem_limit" not in sidecar, (
            f"{name}: roboco-decisions must not carry a mem_limit - the cap "
            "rendered the resolver useless and was deliberately removed "
            "(2fcca85d)"
        )
        assert "ports" not in sidecar or not sidecar["ports"], (
            f"{name}: roboco-decisions must not publish host ports "
            "(internal bridge only)"
        )
        assert sidecar.get("restart") == "unless-stopped", (
            f"{name}: roboco-decisions must restart unless-stopped like every sidecar"
        )


def test_sidecar_not_exposed_on_host_ports_in_build_compose() -> None:
    # Explicit re-pin of the "not on the host" rule for the build compose
    # (covered for both files above; this one names the file in the failure).
    compose = _load(_BUILD_COMPOSE)
    sidecar = _sidecar_service(compose, _BUILD_COMPOSE)
    assert not sidecar.get("ports"), (
        "docker-compose.yml: roboco-decisions must not be exposed on host ports"
    )


def test_sidecar_resolver_url_points_at_container() -> None:
    """The orchestrator env must resolve decisions to the sidecar's
    container name on 8100 - a localhost/loopback default would die inside
    the orchestrator container exactly like the 2026-09-06 dispatcher
    defect (see test_compose_roles.py)."""
    for name in _COMPOSE_FILES:
        compose = _load(name)
        env = compose["x-orchestrator-env"]
        value = str(env.get("ROBOCO_DECISIONS_BASE_URL", ""))
        expected = "${ROBOCO_DECISIONS_BASE_URL:-http://roboco-decisions:8100}"
        assert value == expected, (
            f"{name}: ROBOCO_DECISIONS_BASE_URL must default to "
            f"http://roboco-decisions:8100, got {value}"
        )
        assert "roboco-decisions:8100" in value and not any(
            banned in value for banned in ("127.0.0.1", "localhost", "0.0.0.0")
        ), f"{name}: ROBOCO_DECISIONS_BASE_URL must name the sidecar container"
        assert env.get("ROBOCO_DECISIONS_TIER_CLEF_ENABLED") == (
            "${ROBOCO_DECISIONS_TIER_CLEF_ENABLED:-true}"
        ), f"{name}: the Clef tier is the built-in default and must default ON"
        assert env.get("ROBOCO_DECISIONS_TIER_LAYA_ENABLED") == (
            "${ROBOCO_DECISIONS_TIER_LAYA_ENABLED:-true}"
        ), f"{name}: the cheap Laya tier is the built-in fallback and must default ON"
        assert env.get("ROBOCO_DECISIONS_TIER_OPENROUTER_ENABLED") == (
            "${ROBOCO_DECISIONS_TIER_OPENROUTER_ENABLED:-false}"
        ), f"{name}: the OpenRouter fallback tier is OPT-IN and must default OFF"


def test_pilot_arming_masters_are_split_nas_vs_registry() -> None:
    """Arming posture (CEO decision 2026-09-23): the NAS/dev build compose
    ships the master flag ON with a MIX of pilot modes (cheap-failure and
    advisory pilots ON, expensive-wrong-answer pilots SHADOW); the
    user-facing registry compose ships everything OFF - nothing serves a
    verdict there until the CEO arms pilots from the panel."""
    build_env = _load(_BUILD_COMPOSE)["x-orchestrator-env"]
    registry_env = _load(_REGISTRY_COMPOSE)["x-orchestrator-env"]

    assert build_env.get("ROBOCO_DECISIONS_ENABLED") == (
        "${ROBOCO_DECISIONS_ENABLED:-true}"
    ), "docker-compose.yml: the NAS deploy arms the Decisions master flag"
    assert registry_env.get("ROBOCO_DECISIONS_ENABLED") == (
        "${ROBOCO_DECISIONS_ENABLED:-false}"
    ), "docker-compose.registry.yml: user-facing deploys ship decisions OFF"

    on_slugs = set(str(build_env.get("ROBOCO_DECISIONS_PILOTS_ON", "")).split(","))
    shadow_slugs = set(
        str(build_env.get("ROBOCO_DECISIONS_PILOTS_SHADOW", "")).split(",")
    )
    assert on_slugs and shadow_slugs, (
        "docker-compose.yml: the NAS arming mix needs both lists populated"
    )
    assert not (on_slugs & shadow_slugs), (
        "docker-compose.yml: a pilot cannot be ON and SHADOW at once"
    )
    # The user-facing side gets NO env arming at all: panel rows only.
    assert not registry_env.get("ROBOCO_DECISIONS_PILOTS_ON"), (
        "docker-compose.registry.yml: must not arm pilots via env"
    )
    assert not registry_env.get("ROBOCO_DECISIONS_PILOTS_SHADOW"), (
        "docker-compose.registry.yml: must not arm pilots via env"
    )


def test_sidecar_registry_image_form_matches_sidecar_convention() -> None:
    compose = _load(_REGISTRY_COMPOSE)
    sidecar = _sidecar_service(compose, _REGISTRY_COMPOSE)
    expected_image = (
        "${ROBOCO_REGISTRY:-ghcr.io/rennf93}/roboco-decisions:${ROBOCO_VERSION:-latest}"
    )
    assert sidecar.get("image") == expected_image, (
        "docker-compose.registry.yml: roboco-decisions must pull from "
        "ROBOCO_REGISTRY/ROBOCO_VERSION like every other sidecar"
    )
    assert "build" not in sidecar, (
        "docker-compose.registry.yml: the registry compose never builds from source"
    )


def test_compose_yaml_twin_byte_identical() -> None:
    """The blue-green twin must stay byte-identical (make compose-sync)."""
    yml = (_REPO_ROOT / _BUILD_COMPOSE).read_bytes()
    yaml_twin = (_REPO_ROOT / _BUILD_COMPOSE.replace(".yml", ".yaml")).read_bytes()
    assert yml == yaml_twin, (
        "docker-compose.yaml has drifted from docker-compose.yml - copy .yml over .yaml"
    )


_IGPU_COMPOSE = "docker-compose.igpu.yaml"
_INPUT_CAP = "${ROBOCO_DECISIONS_CLEF_MAX_INPUT_TOKENS:-8192}"


def test_one_input_cap_knob_reaches_the_sidecar_in_both_composes() -> None:
    for name in _COMPOSE_FILES:
        env = _sidecar_service(_load(name), name)["environment"]
        assert env["CLEF_MAX_INPUT_TOKENS"] == _INPUT_CAP, name


def test_the_igpu_override_puts_both_model_servers_on_dri() -> None:
    services = _load(_IGPU_COMPOSE)["services"]
    for name in ("decisions", "ollama"):
        assert services[name]["devices"] == ["/dev/dri:/dev/dri"], name
    # The sidecar runs as a non-root uid: it needs the host's render group.
    assert services["decisions"]["group_add"] == [
        "${ROBOCO_RENDER_GID:?scripts/deploy-nas.sh sets it from /dev/dri/renderD128}"
    ]
    # Without the integrated-GPU opt-in ollama logs "dropping integrated
    # GPU" and stays on the CPU.
    assert services["ollama"]["environment"] == {
        "OLLAMA_VULKAN": "1",
        "OLLAMA_IGPU_ENABLE": "1",
    }


def test_the_igpu_override_names_only_services_the_base_compose_has() -> None:
    base = set(_load(_BUILD_COMPOSE)["services"])
    assert set(_load(_IGPU_COMPOSE)["services"]) <= base


def test_deploy_nas_adds_the_igpu_override_only_when_the_device_exists() -> None:
    script = (_REPO_ROOT / "scripts" / "deploy-nas.sh").read_text()
    guard = "if [ -c /dev/dri/renderD128 ]; then"
    assert guard in script
    block = script[script.index(guard) : script.index("fi", script.index(guard))]
    assert "COMPOSE+=(-f docker-compose.igpu.yaml)" in block
    assert 'ROBOCO_RENDER_GID="$(stat -c %g /dev/dri/renderD128)"' in block
