"""Compose contract for the Docker gate (trail #34).

The host's /var/run/docker.sock is root on the host, so only the gate may
mount it. Every service that reaches Docker (the orchestrator and dispatcher,
blue/green or singular) gets the gate's socket volume instead, a DOCKER_HOST
naming a socket a gate in the same file listens on, and a health dependency
on that gate. The gates themselves run hardened: every capability but CHOWN
and DAC_READ_SEARCH dropped, no network, a read-only root, no-new-privileges.
Image builds pass only through the build file's gates. Stage 2 checks binds
on disk, so each gate's bind roots are the host paths the orchestrator hands
agents (its ROBOCO_HOST_* values), each mounted read-only at its own path.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

_REPO_ROOT = Path(__file__).parents[2]
_HOST_SOCKET = "/var/run/docker.sock"
# The orchestrator's host paths whose values the gate's bind roots repeat
# (the project dir only for its docs/, the one part agents get).
_ROOT_VARIABLES = (
    ("ROBOCO_HOST_DATA_DIR", ""),
    ("ROBOCO_HOST_PROJECT_DIR", "/docs"),
    ("ROBOCO_HOST_CLAUDE_DIR", ""),
    ("ROBOCO_HOST_GROK_DIR", ""),
    ("ROBOCO_HOST_CODEX_DIR", ""),
    ("ROBOCO_HOST_GEMINI_DIR", ""),
    ("ROBOCO_HOST_KIMI_DIR", ""),
)
# File -> whether its gates admit image builds (the build file builds agent
# images locally; the registry file pulls every image).
_COMPOSE_FILES = {
    "docker-compose.yml": True,
    "docker-compose.yaml": True,
    "docker-compose.registry.yml": False,
}


@cache
def _services(compose_file: str) -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load((_REPO_ROOT / compose_file).read_text())
    return cast("dict[str, dict[str, Any]]", doc["services"])


def _gates(compose_file: str) -> dict[str, dict[str, Any]]:
    return {
        name: svc
        for name, svc in _services(compose_file).items()
        if name == "docker-gate" or name.startswith("docker-gate-")
    }


def _docker_users(compose_file: str) -> dict[str, dict[str, Any]]:
    """The services that reach Docker: the api and dispatcher roles."""
    return {
        name: svc
        for name, svc in _services(compose_file).items()
        if (svc.get("environment") or {}).get("ROBOCO_ROLE") in {"api", "dispatcher"}
    }


def _bind_roots(gate: dict[str, Any]) -> list[str]:
    """GATE_BIND_ROOTS split on its separators, not on the ``:-`` inside a
    ``${VAR:-default}``."""
    roots, current, depth = [], "", 0
    for char in gate["environment"]["GATE_BIND_ROOTS"]:
        depth += {"{": 1, "}": -1}.get(char, 0)
        if char == ":" and depth == 0:
            roots.append(current)
            current = ""
        else:
            current += char
    return [*roots, current]


def _volume_specs(svc: dict[str, Any]) -> list[str]:
    return [
        v if isinstance(v, str) else f"{v['source']}:{v['target']}"
        for v in svc.get("volumes", [])
    ]


@pytest.mark.parametrize("compose_file", sorted(_COMPOSE_FILES))
def test_only_the_gates_mount_the_host_socket(compose_file: str) -> None:
    mounting = sorted(
        name
        for name, svc in _services(compose_file).items()
        if any(spec.split(":")[0] == _HOST_SOCKET for spec in _volume_specs(svc))
    )
    assert mounting == sorted(_gates(compose_file))
    assert mounting


@pytest.mark.parametrize("compose_file", sorted(_COMPOSE_FILES))
def test_every_docker_user_talks_to_a_gate_of_its_own_file(compose_file: str) -> None:
    listening = {
        f"unix://{gate['environment']['GATE_LISTEN']}": name
        for name, gate in _gates(compose_file).items()
    }
    users = _docker_users(compose_file)
    assert users
    for name, svc in users.items():
        docker_host = svc["environment"].get("DOCKER_HOST")
        assert docker_host in listening, f"{name}: DOCKER_HOST {docker_host!r}"
        gate = listening[docker_host]
        assert svc.get("depends_on", {}).get(gate) == {
            "condition": "service_healthy"
        }, name
        assert "docker-gate:/run/roboco-docker" in _volume_specs(svc), name


def test_each_color_uses_its_own_gate() -> None:
    for name, svc in _docker_users("docker-compose.yml").items():
        color = name.rsplit("-", 1)[1]
        assert (
            svc["environment"]["DOCKER_HOST"]
            == f"unix:///run/roboco-docker/{color}.sock"
        )


@pytest.mark.parametrize(("compose_file", "builds"), sorted(_COMPOSE_FILES.items()))
def test_the_gates_run_hardened(compose_file: str, builds: bool) -> None:
    gates = _gates(compose_file)
    assert gates
    for name, gate in gates.items():
        assert gate["user"] == "0:0", name
        assert gate["network_mode"] == "none", name
        assert gate["read_only"] is True, name
        assert gate["cap_drop"] == ["ALL"], name
        assert gate["cap_add"] == ["CHOWN", "DAC_READ_SEARCH"], name
        assert gate["security_opt"] == ["no-new-privileges:true"], name
        assert gate["environment"]["GATE_ALLOW_BUILD"] == (
            "true" if builds else "false"
        ), name
        assert f"{_HOST_SOCKET}:{_HOST_SOCKET}" in _volume_specs(gate), name


@pytest.mark.parametrize("compose_file", sorted(_COMPOSE_FILES))
def test_the_bind_roots_are_the_orchestrators_host_paths(compose_file: str) -> None:
    for user_name, user in _docker_users(compose_file).items():
        env = user["environment"]
        expected = [env[variable] + suffix for variable, suffix in _ROOT_VARIABLES]
        for name, gate in _gates(compose_file).items():
            assert _bind_roots(gate) == expected, f"{name} vs {user_name}"


@pytest.mark.parametrize("compose_file", sorted(_COMPOSE_FILES))
def test_each_bind_root_is_mounted_read_only_at_its_own_path(compose_file: str) -> None:
    for name, gate in _gates(compose_file).items():
        specs = _volume_specs(gate)
        for root in _bind_roots(gate):
            assert f"{root}:{root}:ro" in specs, f"{name}: {root}"


@pytest.mark.parametrize(("compose_file", "builds"), sorted(_COMPOSE_FILES.items()))
def test_only_the_registry_gate_names_a_registry(
    compose_file: str, builds: bool
) -> None:
    for name, gate in _gates(compose_file).items():
        registry = gate["environment"].get("GATE_REGISTRY")
        assert registry == (
            None if builds else "${ROBOCO_REGISTRY:-ghcr.io/rennf93}"
        ), name
