"""Compose contract for the Docker gate (trail #34).

The host's /var/run/docker.sock is root on the host, so only the gate may
mount it. Every service that reaches Docker (the orchestrator and dispatcher,
blue/green or singular) gets the gate's socket volume instead, a DOCKER_HOST
naming a socket a gate in the same file listens on, and a health dependency
on that gate. The gates themselves run hardened: every capability but CHOWN
dropped, no network, a read-only root, no-new-privileges. Image builds pass
only through the build file's gates.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

_REPO_ROOT = Path(__file__).parents[2]
_HOST_SOCKET = "/var/run/docker.sock"
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
        assert gate["cap_add"] == ["CHOWN"], name
        assert gate["security_opt"] == ["no-new-privileges:true"], name
        assert gate["environment"]["GATE_ALLOW_BUILD"] == (
            "true" if builds else "false"
        ), name
        assert f"{_HOST_SOCKET}:{_HOST_SOCKET}" in _volume_specs(gate), name
