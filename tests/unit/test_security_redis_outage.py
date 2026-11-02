"""A Redis outage degrades only the ban lookup, never the IP allowlist.

``IpSecurityCheck.check`` reads the ban store first; a ``GuardRedisError``
there made guard-core's pipeline skip the WHOLE check under
``redis_fail_open``, and the allowlist decision plus the ``is_whitelisted``
stamp live in that same check. 2026-10-28 05:35-05:36, while the guard's
Redis client hit "Too many connections": "Skipping check ip_security: Redis
unavailable" 192 times, and in exactly those minutes the CEO's tailnet panel
(whitelisted bridge IP 172.23.0.1) was rate-limited 48 times ("Rate limit
exceeded for IP: 172.23.0.1 (121 requests in 60s window)", in-memory
fallback) and WAF-scanned 7 times. Any non-member IP was admitted for the
same window.
"""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import TYPE_CHECKING, NoReturn
from unittest.mock import MagicMock

import pytest
from guard_core.exceptions import GuardRedisError
from guard_core.handlers.ipban_handler import IPBanManager, ip_ban_manager
from tests.unit.test_security_middleware import (
    _BENIGN_BODY,
    _CMD_INJECTION_BODY,
    _DOCKER_BRIDGE_PEER,
    _EXTERNAL_PEER,
    _TAILNET_PEER,
    _client,
    _guarded_gateway_app,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


async def _redis_too_many_connections(*_args: object, **_kwargs: object) -> NoReturn:
    raise GuardRedisError(503, "Redis operation failed: Too many connections")


@pytest.fixture
def ban_store_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every ban lookup that reaches Redis fails, as on 2026-10-28 05:35."""
    monkeypatch.setattr(IPBanManager, "_check_redis_exact", _redis_too_many_connections)


def _arm_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ip_ban_manager, "redis_handler", MagicMock())


def _post_panel(client: TestClient, body: dict[str, str]) -> int:
    return client.post("/api/optimal/panel_probe", json=body).status_code


@pytest.mark.usefixtures("ban_store_down")
class TestBanStoreOutage:
    def test_tailnet_peer_is_not_rate_limited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _client(_guarded_gateway_app(ip=_TAILNET_PEER, rate_limit=2)) as client:
            _arm_redis(monkeypatch)
            statuses = [_post_panel(client, _BENIGN_BODY) for _ in range(5)]
        assert statuses == [HTTPStatus.OK] * 5

    def test_mesh_peer_keeps_its_waf_exemption(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _client(_guarded_gateway_app(ip=_DOCKER_BRIDGE_PEER)) as client:
            _arm_redis(monkeypatch)
            status = _post_panel(client, _CMD_INJECTION_BODY)
        assert status == HTTPStatus.OK

    def test_non_member_is_still_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _client(_guarded_gateway_app(ip=_EXTERNAL_PEER)) as client:
            _arm_redis(monkeypatch)
            status = _post_panel(client, _BENIGN_BODY)
        assert status == HTTPStatus.FORBIDDEN

    def test_a_ban_this_process_knows_still_applies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(ip_ban_manager.banned_ips, _TAILNET_PEER, time.time() + 60)
        with _client(_guarded_gateway_app(ip=_TAILNET_PEER)) as client:
            _arm_redis(monkeypatch)
            status = _post_panel(client, _BENIGN_BODY)
        assert status == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
@pytest.mark.usefixtures("ban_store_down")
async def test_ban_lookup_still_raises_without_redis_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm_redis(monkeypatch)
    monkeypatch.setattr(ip_ban_manager, "config", MagicMock(redis_fail_open=False))
    with pytest.raises(GuardRedisError):
        await ip_ban_manager.is_ip_banned("203.0.113.90")
