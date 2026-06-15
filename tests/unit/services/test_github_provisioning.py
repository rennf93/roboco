"""roboco.services.github_provisioning — repo creation against MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from roboco.config import settings
from roboco.services.github_provisioning import (
    GitHubProvisioningService,
    ProvisioningDisabledError,
    ProvisioningError,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_disabled_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "provisioning_enabled", True)
    svc = GitHubProvisioningService(
        token="", org="", client=_client(lambda _r: httpx.Response(201))
    )
    assert svc.enabled is False
    with pytest.raises(ProvisioningDisabledError):
        await svc.create_repo("x")


@pytest.mark.asyncio
async def test_disabled_when_master_switch_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "provisioning_enabled", False)
    svc = GitHubProvisioningService(
        token="tok", org="acme", client=_client(lambda _r: httpx.Response(201))
    )
    assert svc.enabled is False


@pytest.mark.asyncio
async def test_create_repo_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "provisioning_enabled", True)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/orgs/acme/repos"
        body = json.loads(request.content)
        assert body["name"] == "newrepo"
        assert body["auto_init"] is True
        assert body["private"] is True
        return httpx.Response(
            201,
            json={
                "full_name": "acme/newrepo",
                "clone_url": "https://github.com/acme/newrepo.git",
                "html_url": "https://github.com/acme/newrepo",
            },
        )

    svc = GitHubProvisioningService(token="tok", org="acme", client=_client(handler))
    assert svc.enabled is True
    repo = await svc.create_repo("newrepo", "desc")
    assert repo.full_name == "acme/newrepo"
    assert repo.clone_url.endswith("newrepo.git")


@pytest.mark.asyncio
async def test_create_repo_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "provisioning_enabled", True)
    svc = GitHubProvisioningService(
        token="tok",
        org="acme",
        client=_client(lambda _r: httpx.Response(422, text="name exists")),
    )
    with pytest.raises(ProvisioningError):
        await svc.create_repo("dup")


@pytest.mark.asyncio
async def test_network_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "provisioning_enabled", True)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    svc = GitHubProvisioningService(token="tok", org="acme", client=_client(handler))
    with pytest.raises(ProvisioningError):
        await svc.create_repo("x")
