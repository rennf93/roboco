"""Host-proxied tailnet client-IP resolution for the guard.

fastapi-guard peels a fixed trusted_proxy_depth=1 from X-Forwarded-For (the
rightmost entry, which nginx itself recorded). That is correct for every
chain except host-proxied tailnet traffic (Tailscale Serve → nginx), which
arrives as ``[tailnet-client, <loopback-or-configured-gateway>]`` — depth-1
resolves it to a whitelisted hop IP and the WAF goes inert for /tg.
``ClientIpResolutionMiddleware`` stamps guard_core's ``state.client_ip``
cache (the supported pre-resolution seam) for EXACTLY that shape and
abstains on every other, so no path resolves differently from the depth-1
baseline unless the candidate is a tailnet CGNAT address behind real hops.

The XFF hop-peel set is loopback ALWAYS plus operator-named
``guard_trusted_chain_peers`` (default empty) — NOT the whole docker bridge
pool, so an unnamed 172.x address can never be treated as a hop.
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


def _configure_chain_peers(monkeypatch: pytest.MonkeyPatch, csv: str) -> None:
    """Set guard_trusted_chain_peers and rebuild the effective hop set.

    Mirrors what config load does once at import time; tests need to redo
    it per-case since ``_TRUSTED_HOP_NETWORKS`` is otherwise built once.
    """
    monkeypatch.setattr(settings, "guard_trusted_chain_peers", csv)
    monkeypatch.setattr(
        security, "_TRUSTED_HOP_NETWORKS", security._build_trusted_hop_networks()
    )


# ---------------------------------------------------------------------------
# resolve_forwarded_client_ip — stamps ONLY the tailnet-behind-hops shape
# ---------------------------------------------------------------------------


def test_tailscale_serve_behind_loopback_resolves_tailnet_peer() -> None:
    assert (
        resolve_forwarded_client_ip("100.101.102.103, 127.0.0.1") == "100.101.102.103"
    )


def test_tailscale_serve_behind_bridge_gateway_resolves_tailnet_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Docker DNAT presents host-originated connections as the bridge gateway,
    # so nginx may record 172.x instead of loopback for Tailscale Serve. That
    # 172.x address only peels once the operator names it explicitly.
    _configure_chain_peers(monkeypatch, "172.18.0.1")
    assert (
        resolve_forwarded_client_ip("100.101.102.103, 172.18.0.1") == "100.101.102.103"
    )


def test_bridge_gateway_unconfigured_by_default_never_peels() -> None:
    # Same chain as above, but with NO configured chain peers (the default):
    # the 172.x gateway is no longer a recognized hop, so the resolver
    # abstains and depth-1 keeps resolving to the gateway IP itself.
    assert resolve_forwarded_client_ip("100.101.102.103, 172.18.0.1") is None


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


def test_default_empty_chain_peers_closes_the_forge_residual() -> None:
    # THE fixed residual: a same-bridge container relaying a forged
    # tailnet-CGNAT XFF prefix used to have its unnamed 172.x rightmost
    # entry peeled as a "trusted hop" (the whole /12 was the hop set),
    # stamping the forged CGNAT address. With no chain peers configured
    # (the default), 172.20.0.7 is never a recognized hop, so no hop is
    # peeled and the resolver abstains — the forge no longer lands.
    assert resolve_forwarded_client_ip("100.99.1.1, 172.20.0.7") is None


def test_configured_peer_does_not_extend_to_other_bridge_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even with a real chain peer configured (the docker gateway), a forger
    # relaying through its OWN bridge IP — never the reserved gateway
    # address — still abstains: only the exact configured peer(s) peel, not
    # the whole bridge pool.
    _configure_chain_peers(monkeypatch, "172.18.0.1")
    assert resolve_forwarded_client_ip("100.99.1.1, 172.20.0.7") is None


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


def test_loopback_hop_chains_unaffected_by_peer_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Loopback is unconditionally part of the hop set regardless of what
    # (if anything) is configured — a chain peer config only ADDS to it.
    _configure_chain_peers(monkeypatch, "172.18.0.1")
    assert (
        resolve_forwarded_client_ip("100.101.102.103, 127.0.0.1") == "100.101.102.103"
    )
    assert resolve_forwarded_client_ip("127.0.0.1") is None


# ---------------------------------------------------------------------------
# _build_trusted_hop_networks — csv parsing at config-load time
# ---------------------------------------------------------------------------


def test_invalid_chain_peer_entry_skipped_without_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings, "guard_trusted_chain_peers", "not-a-cidr, 172.18.0.1, "
    )
    networks = security._build_trusted_hop_networks()
    assert networks == ("127.0.0.1/32", "::1/128", "172.18.0.1/32")


def test_empty_chain_peers_yields_loopback_only() -> None:
    assert security._build_trusted_hop_networks() == ("127.0.0.1/32", "::1/128")


def test_plain_ip_chain_peer_stored_as_its_own_slash_32(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "guard_trusted_chain_peers", "172.18.0.1")
    assert security._build_trusted_hop_networks() == (
        "127.0.0.1/32",
        "::1/128",
        "172.18.0.1/32",
    )


def test_subnet_chain_peer_entry_rejected_and_not_in_hop_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Docker's typical bridge allocation — an entirely plausible operator
    # copy-paste — must be rejected, not admitted: a range would readmit
    # every sibling container's real address into the hop set.
    monkeypatch.setattr(settings, "guard_trusted_chain_peers", "172.16.0.0/12")
    assert security._build_trusted_hop_networks() == ("127.0.0.1/32", "::1/128")
    # And the forge chain a wrongly-admitted /12 would have reopened still
    # abstains end-to-end.
    _configure_chain_peers(monkeypatch, "172.16.0.0/12")
    assert resolve_forwarded_client_ip("100.99.1.1, 172.20.0.7") is None


def test_host_bits_typo_cidr_rejected_same_as_a_subnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo'd CIDR the operator meant as a plain address — ip_network's
    # default strict=True would silently accept this; ip_address rejects it.
    monkeypatch.setattr(settings, "guard_trusted_chain_peers", "172.18.0.5/24")
    assert security._build_trusted_hop_networks() == ("127.0.0.1/32", "::1/128")


# ---------------------------------------------------------------------------
# _warn_unconfigured_tailnet_gateway_once — the silent-default-gap signal
# ---------------------------------------------------------------------------


def test_unconfigured_gateway_chain_warns_once_per_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(security, "_WARNED_UNCONFIGURED_GATEWAYS", set())
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        security.logger, "warning", lambda *a, **kw: calls.append((a, kw))
    )
    for _ in range(3):
        assert resolve_forwarded_client_ip("100.101.102.103, 172.19.0.9") is None
    assert len(calls) == 1
    assert "172.19.0.9" in calls[0][0][0]


def test_configured_peer_suppresses_the_gap_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A DIFFERENT gateway than the one configured would otherwise trip the
    # detection, but ANY configured peer means the operator has already
    # addressed the awareness gap — the warning never fires at all.
    monkeypatch.setattr(security, "_WARNED_UNCONFIGURED_GATEWAYS", set())
    _configure_chain_peers(monkeypatch, "172.18.0.1")
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        security.logger, "warning", lambda *a, **kw: calls.append((a, kw))
    )
    assert resolve_forwarded_client_ip("100.99.1.1, 172.20.0.7") is None
    assert calls == []


def test_non_host_proxied_shapes_never_trigger_the_gap_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(security, "_WARNED_UNCONFIGURED_GATEWAYS", set())
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        security.logger, "warning", lambda *a, **kw: calls.append((a, kw))
    )
    # A forged public-IP prefix behind a bridge address isn't the tailnet
    # (CGNAT) shape.
    resolve_forwarded_client_ip("9.9.9.9, 172.20.0.7")
    # A LAN client behind a genuine loopback hop resolves — a hop WAS
    # peeled, so this never reaches the unconfigured-gateway branch.
    resolve_forwarded_client_ip("203.0.113.9, 127.0.0.1")
    assert calls == []


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
