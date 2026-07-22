"""Host-proxied tailnet client-IP resolution for the guard.

fastapi-guard peels a fixed trusted_proxy_depth=1 from X-Forwarded-For (the
rightmost entry, which nginx itself recorded). That is correct for every
chain except host-proxied tailnet traffic (Tailscale Serve → nginx), which
arrives as ``[tailnet-client, <loopback-or-bridge-gateway>]`` — depth-1
resolves it to a whitelisted hop IP and the WAF goes inert for /tg.
``ClientIpResolutionMiddleware`` stamps guard_core's ``state.client_ip``
cache (the supported pre-resolution seam) for EXACTLY that shape and
abstains on every other, so no path resolves differently from the depth-1
baseline unless the candidate is a tailnet CGNAT address behind real hops.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from fastapi import FastAPI
from guard.adapters import StarletteGuardRequest
from guard_core.utils import extract_client_ip
from roboco import security
from roboco.config import settings
from roboco.security import (
    ClientIpResolutionMiddleware,
    resolve_forwarded_client_ip,
)
from starlette.requests import Request

# ---------------------------------------------------------------------------
# resolve_forwarded_client_ip — stamps ONLY the tailnet-behind-hops shape
# ---------------------------------------------------------------------------


def test_tailscale_serve_behind_loopback_resolves_tailnet_peer() -> None:
    assert (
        resolve_forwarded_client_ip("100.101.102.103, 127.0.0.1") == "100.101.102.103"
    )


def test_tailscale_serve_behind_bridge_gateway_resolves_tailnet_peer() -> None:
    # Docker DNAT presents host-originated connections as the bridge gateway,
    # so nginx may record 172.x instead of loopback for Tailscale Serve.
    assert (
        resolve_forwarded_client_ip("100.101.102.103, 172.18.0.1") == "100.101.102.103"
    )


def test_forged_prefix_behind_tailscale_chain_ignored() -> None:
    assert (
        resolve_forwarded_client_ip("6.6.6.6, 100.101.102.103, 127.0.0.1")
        == "100.101.102.103"
    )


def test_lan_client_single_entry_abstains() -> None:
    # Depth-1 already resolves this correctly; the resolver must not engage.
    assert resolve_forwarded_client_ip("192.168.1.50") is None


def test_bridge_peer_with_forged_public_prefix_abstains() -> None:
    # THE regression case: a same-bridge container relays through nginx with
    # a forged public-IP prefix; nginx appends the container's real 172.x.
    # Content-based recursion would hand the attacker "9.9.9.9" — the
    # resolver must abstain so the guard's depth-1 keeps the real bridge IP.
    assert resolve_forwarded_client_ip("9.9.9.9, 172.20.0.7") is None


def test_bridge_peer_forging_tailnet_prefix_only_deprivileges() -> None:
    # Documented residual: forging a CGNAT prefix IS stamped — the forger
    # loses its whitelist exemption (fake tailnet IPs eat the WAF); it can
    # never gain privilege this way.
    assert resolve_forwarded_client_ip("100.99.1.1, 172.20.0.7") == "100.99.1.1"


def test_non_tailnet_client_behind_hop_abstains() -> None:
    # A public/LAN client behind a genuine hop is left to the baseline.
    assert resolve_forwarded_client_ip("203.0.113.9, 127.0.0.1") is None
    assert resolve_forwarded_client_ip("192.168.1.50, 127.0.0.1") is None


def test_all_hops_chain_abstains() -> None:
    # Operator curl on the host: baseline resolves to a whitelisted hop
    # already; nothing to fix, so abstain.
    assert resolve_forwarded_client_ip("127.0.0.1") is None
    assert resolve_forwarded_client_ip("172.18.0.1, 127.0.0.1") is None


def test_malformed_entries_abstain() -> None:
    assert resolve_forwarded_client_ip("not-an-ip, 127.0.0.1") is None
    assert resolve_forwarded_client_ip("") is None
    assert resolve_forwarded_client_ip(" , ") is None
    assert resolve_forwarded_client_ip("100.99.1.1:443, 127.0.0.1") is None


# ---------------------------------------------------------------------------
# ClientIpResolutionMiddleware stamping
# ---------------------------------------------------------------------------


async def _run_middleware(scope: dict[str, Any]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def app(inner_scope: Any, _receive: Any, _send: Any) -> None:
        captured.update(inner_scope)

    async def receive() -> dict[str, Any]:  # pragma: no cover - never called
        return {}

    async def send(_message: Any) -> None:  # pragma: no cover - never called
        return None

    await ClientIpResolutionMiddleware(app)(scope, receive, send)
    return captured


@pytest.mark.asyncio
async def test_stamps_state_for_trusted_connecting_hop() -> None:
    scope = {
        "type": "http",
        "client": ("172.18.0.5", 1234),  # nginx on the docker bridge
        "headers": [(b"x-forwarded-for", b"100.101.102.103, 127.0.0.1")],
    }
    seen = await _run_middleware(scope)
    assert seen["state"]["client_ip"] == "100.101.102.103"


@pytest.mark.asyncio
async def test_untrusted_connecting_peer_is_not_consulted() -> None:
    # A directly-connected client's forged XFF must not be honored here.
    scope = {
        "type": "http",
        "client": ("192.168.1.9", 1234),
        "headers": [(b"x-forwarded-for", b"100.99.1.1, 127.0.0.1")],
    }
    seen = await _run_middleware(scope)
    assert "state" not in seen or "client_ip" not in seen.get("state", {})


@pytest.mark.asyncio
async def test_duplicate_forwarded_headers_use_first_occurrence() -> None:
    # Starlette's Headers.get returns the FIRST occurrence — this layer must
    # read the same value the guard's own fallback would.
    scope = {
        "type": "http",
        "client": ("172.18.0.5", 1234),
        "headers": [
            (b"x-forwarded-for", b"100.101.102.103, 127.0.0.1"),
            (b"x-forwarded-for", b"100.66.6.6, 127.0.0.1"),
        ],
    }
    seen = await _run_middleware(scope)
    assert seen["state"]["client_ip"] == "100.101.102.103"


@pytest.mark.asyncio
async def test_abstain_stamps_nothing() -> None:
    scope = {
        "type": "http",
        "client": ("172.18.0.5", 1234),
        "headers": [(b"x-forwarded-for", b"9.9.9.9, 172.20.0.7")],
    }
    seen = await _run_middleware(scope)
    assert "state" not in seen or "client_ip" not in seen.get("state", {})


@pytest.mark.asyncio
async def test_non_http_scope_passthrough() -> None:
    scope = {"type": "websocket", "client": ("172.18.0.5", 1234)}
    seen = await _run_middleware(scope)
    assert "state" not in seen


# ---------------------------------------------------------------------------
# Wiring: mount presence AND order (resolver must run before the guard)
# ---------------------------------------------------------------------------


def test_apply_guard_mounts_resolver_outermost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "guard_enabled", True)
    app = FastAPI()
    security.apply_guard(app)
    names = [getattr(m.cls, "__name__", str(m.cls)) for m in app.user_middleware]
    assert "ClientIpResolutionMiddleware" in names
    assert "SecurityMiddleware" in names
    # Starlette runs user_middleware in list order (index 0 = outermost), so
    # the resolver must sit BEFORE the guard or the stamp arrives too late.
    assert names.index("ClientIpResolutionMiddleware") < names.index(
        "SecurityMiddleware"
    )


@pytest.mark.asyncio
async def test_guard_extract_honors_stamped_state() -> None:
    """End-to-end seam check: guard_core's extractor returns the stamp."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/tg",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"100.101.102.103, 127.0.0.1")],
        "client": ("172.18.0.5", 1234),
        "state": {"client_ip": "100.101.102.103"},
    }
    request = StarletteGuardRequest(Request(scope))

    class _Cfg:
        trusted_proxies: ClassVar[list[str]] = ["127.0.0.1", "172.16.0.0/12"]
        trusted_proxy_depth = 1

    assert await extract_client_ip(request, _Cfg()) == "100.101.102.103"
