"""Compose contract: the NAS compose files trust the roboco_default gateway.

Tailscale Serve traffic reaches nginx from that gateway, so the guard must
peel it as an X-Forwarded-For hop (ROBOCO_GUARD_TRUSTED_CHAIN_PEERS). The
subnet is pinned so the gateway is a constant, not whatever docker picks when
it creates the network.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any

import pytest
import yaml
from roboco.security import _DOCKER_BRIDGE_POOL

_REPO_ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.yaml"])
def test_the_trusted_chain_peer_is_the_pinned_gateway(compose_file: str) -> None:
    compose: dict[str, Any] = yaml.safe_load((_REPO_ROOT / compose_file).read_text())
    (pool,) = compose["networks"]["default"]["ipam"]["config"]
    subnet = ipaddress.IPv4Network(pool["subnet"])
    gateway = ipaddress.IPv4Address(pool["gateway"])
    # nginx's connecting address must stay inside the peer gate's bridge pool.
    assert subnet.subnet_of(ipaddress.IPv4Network(_DOCKER_BRIDGE_POOL))
    assert gateway in subnet
    peers = {
        service["environment"]["ROBOCO_GUARD_TRUSTED_CHAIN_PEERS"]
        for name, service in compose["services"].items()
        if name.startswith("orchestrator-")
    }
    assert peers == {str(gateway)}
