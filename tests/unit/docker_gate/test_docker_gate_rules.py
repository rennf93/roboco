"""The Docker gate's stage-1 allowlist: the calls RoboCo makes, and only those.

``docker/docker-gate/gate.py`` decides by method and path (after the
``/v1.NN`` prefix), plus the container name on ``POST /containers/create``.
"""

from __future__ import annotations

import importlib.util
import sys
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_GATE = Path(__file__).parents[3] / "docker" / "docker-gate" / "gate.py"
AGENT = "roboco-agent-0b6e1f2a-4c1d-4b8e-9a77-2d1c3e4f5a6b"
WSX = "roboco-wsx-0123456789ab"
SANDBOX = "roboco-sandbox-pg-0b6e1f2a"
ID = "4f1c2b3a9d8e"


@cache
def _gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("docker_gate", _GATE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look their module up in sys.modules while the class is built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _decide(method: str, target: str, *, allow_build: bool = False) -> str | None:
    return cast("str | None", _gate().decide(method, target, allow_build=allow_build))


@pytest.mark.parametrize(
    ("method", "target"),
    [
        ("HEAD", "/_ping"),
        ("GET", "/_ping"),
        ("GET", "/v1.51/version"),
        (
            "GET",
            "/v1.51/containers/json?filters=%7B%22label%22%3A%5B%22roboco.sandbox%3D1%22%5D%7D",
        ),
        ("GET", f"/v1.51/containers/{AGENT}/json"),
        ("GET", f"/v1.51/containers/{ID}/json"),
        ("GET", f"/v1.51/containers/{AGENT}/logs?stderr=1&stdout=1&tail=200"),
        ("GET", f"/v1.51/containers/{ID}/logs?since=60&stderr=1&stdout=1"),
        ("POST", f"/v1.51/containers/create?name={WSX}"),
        ("POST", f"/v1.51/containers/create?name={SANDBOX}"),
        ("POST", f"/v1.51/containers/{AGENT}/start"),
        ("POST", f"/v1.51/containers/{ID}/attach?stderr=1&stdin=1&stdout=1&stream=1"),
        ("POST", f"/v1.51/containers/{ID}/wait?condition=removed"),
        ("POST", f"/v1.51/containers/{AGENT}/stop?t=10"),
        ("POST", f"/v1.51/containers/{WSX}/kill"),
        ("DELETE", f"/v1.51/containers/{AGENT}?force=1"),
        ("POST", f"/v1.51/containers/{AGENT}/exec"),
        ("POST", f"/v1.51/exec/{ID}/start"),
        ("GET", f"/v1.51/exec/{ID}/json"),
        ("GET", "/v1.51/images/roboco-agent-codex:latest/json"),
        ("GET", "/v1.51/images/ghcr.io/rennf93/roboco-agent-base:0.32.0/json"),
        ("POST", "/v1.51/images/create?fromImage=roboco-sandbox-pg&tag=latest"),
        ("POST", "/v1.51/images/prune?filters=%7B%22dangling%22%3A%5B%22true%22%5D%7D"),
    ],
)
def test_the_calls_roboco_makes_pass(method: str, target: str) -> None:
    assert _decide(method, target) is None


@pytest.mark.parametrize(
    ("method", "target"),
    [
        # Container create without a roboco name, or with someone else's.
        ("POST", "/v1.51/containers/create"),
        ("POST", "/v1.51/containers/create?name=roboco-postgres"),
        ("POST", "/v1.51/containers/create?name=evil"),
        ("POST", f"/v1.51/containers/create?name={AGENT}&name=evil"),
        # Exec into a container that is not an agent's, a sandbox or a wsx.
        ("POST", "/v1.51/containers/roboco-postgres/exec"),
        ("POST", f"/v1.51/containers/{ID}/exec"),
        # Remove or read the logs of anything else.
        ("DELETE", "/v1.51/containers/roboco-postgres"),
        ("DELETE", f"/v1.51/containers/{ID}"),
        ("GET", "/v1.51/containers/roboco-postgres/logs"),
        # The rest of the Engine API.
        ("GET", "/v1.51/info"),
        ("GET", "/v1.51/events"),
        ("PUT", f"/v1.51/containers/{AGENT}/archive?path=/"),
        ("GET", f"/v1.51/containers/{AGENT}/export"),
        ("POST", f"/v1.51/containers/{AGENT}/update"),
        ("POST", f"/v1.51/containers/{AGENT}/rename?name=x"),
        ("POST", "/v1.51/commit?container=x"),
        ("POST", "/v1.51/images/x/push"),
        ("POST", "/v1.51/images/x/tag?repo=y"),
        ("POST", "/v1.51/images/load"),
        ("GET", "/v1.51/images/get"),
        ("POST", "/v1.51/networks/create"),
        ("POST", "/v1.51/volumes/create"),
        ("POST", "/v1.51/swarm/init"),
        ("POST", "/v1.51/auth"),
        ("GET", "/v1.51/plugins"),
        # The right path with the wrong method.
        ("DELETE", "/v1.51/images/x"),
        ("POST", "/v1.51/containers/json"),
    ],
)
def test_everything_else_is_refused(method: str, target: str) -> None:
    assert _decide(method, target) is not None


@pytest.mark.parametrize(
    "target", ["/v1.51/build?t=roboco-agent-base", "/v1.51/session", "/v1.51/grpc"]
)
def test_builds_pass_only_when_the_gate_allows_them(target: str) -> None:
    # CLI 29 builds through BuildKit's /session and /grpc tunnel, not /build.
    assert _decide("POST", target) is not None
    assert _decide("POST", target, allow_build=True) is None


def test_a_refusal_says_why() -> None:
    reason = _decide("POST", "/v1.51/containers/create?name=evil")
    assert reason is not None
    assert "roboco-agent" in reason
