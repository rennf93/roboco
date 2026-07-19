"""roboco.services.github_provisioning — repo creation against MockTransport.

Provider-aware since Phase 4: the same service now also targets GitLab/Gitea
(``provider_name=``/``host=``), so this file covers github (unchanged default)
plus one success + one idempotent-reuse case per additional forge, and a
config-default regression guard (no provider set => GitHubProvider).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from roboco.config import settings
from roboco.services.forge.github import GitHubProvider
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


@pytest.mark.asyncio
async def test_create_repo_reuses_already_existing_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#83/#84: a 422 'name already exists' (a repo orphaned by a rolled-back
    prior approval) is fetched and returned, not errored — so re-approval reuses
    the GitHub repo instead of colliding."""
    monkeypatch.setattr(settings, "provisioning_enabled", True)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(
                422, text='{"message": "name already exists on this account"}'
            )
        if request.url.path == "/repos/acme/orphan":
            return httpx.Response(
                200,
                json={
                    "full_name": "acme/orphan",
                    "clone_url": "https://github.com/acme/orphan.git",
                    "html_url": "https://github.com/acme/orphan",
                },
            )
        return httpx.Response(404)

    svc = GitHubProvisioningService(token="tok", org="acme", client=_client(handler))
    repo = await svc.create_repo("orphan", "desc")
    assert repo.full_name == "acme/orphan"
    assert repo.clone_url.endswith("orphan.git")
    # POST create -> 422, then GET the existing repo.
    assert calls == ["/orgs/acme/repos", "/repos/acme/orphan"]


@pytest.mark.asyncio
async def test_create_repo_other_422_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 422 that is NOT the 'already exists' shape (e.g. a validation error) is
    still a hard failure — only the orphaned-repo case is treated idempotently."""
    monkeypatch.setattr(settings, "provisioning_enabled", True)
    svc = GitHubProvisioningService(
        token="tok",
        org="acme",
        client=_client(lambda _r: httpx.Response(422, text="name reserved")),
    )
    with pytest.raises(ProvisioningError):
        await svc.create_repo("dup")


def test_default_provider_builds_github_provider() -> None:
    """Regression guard: no provider_name/ROBOCO_PROVISIONING_PROVIDER override
    => the service still builds a plain GitHubProvider, byte-for-byte the
    Phase-1 default."""
    svc = GitHubProvisioningService(token="tok", org="acme")
    assert isinstance(svc._provider, GitHubProvider)


@pytest.mark.asyncio
async def test_gitlab_provider_creates_project_and_reshapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "provisioning_enabled", True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url.path == "/api/v4/groups/acme"
            return httpx.Response(200, json={"id": 7})
        assert request.url.path == "/api/v4/projects"
        return httpx.Response(
            201,
            json={
                "path_with_namespace": "acme/newrepo",
                "web_url": "https://gitlab.example.com/acme/newrepo",
                "http_url_to_repo": "https://gitlab.example.com/acme/newrepo.git",
            },
        )

    svc = GitHubProvisioningService(
        token="tok",
        org="acme",
        provider_name="gitlab",
        host="gitlab.example.com",
        client=_client(handler),
    )
    assert svc.enabled is True
    repo = await svc.create_repo("newrepo", "desc")
    assert repo.full_name == "acme/newrepo"
    assert repo.clone_url.endswith("newrepo.git")


@pytest.mark.asyncio
async def test_gitlab_provider_reuses_already_existing_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitLab's duplicate-path 400 ('has already been taken') reshapes to
    422 so the service's already-exists branch fires exactly like GitHub's."""
    monkeypatch.setattr(settings, "provisioning_enabled", True)
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET" and request.url.path == "/api/v4/groups/acme":
            return httpx.Response(200, json={"id": 7})
        if request.method == "POST" and request.url.path == "/api/v4/projects":
            return httpx.Response(
                400, json={"message": {"path": ["has already been taken"]}}
            )
        if str(request.url).endswith("/api/v4/projects/acme%2Forphan"):
            return httpx.Response(
                200,
                json={
                    "path_with_namespace": "acme/orphan",
                    "web_url": "https://gitlab.example.com/acme/orphan",
                    "http_url_to_repo": "https://gitlab.example.com/acme/orphan.git",
                },
            )
        return httpx.Response(404)

    svc = GitHubProvisioningService(
        token="tok",
        org="acme",
        provider_name="gitlab",
        host="gitlab.example.com",
        client=_client(handler),
    )
    repo = await svc.create_repo("orphan", "desc")
    assert repo.full_name == "acme/orphan"
    assert repo.clone_url.endswith("orphan.git")
    assert calls[-1][0] == "GET"
    assert calls[-1][1].endswith("/api/v4/projects/acme%2Forphan")


@pytest.mark.asyncio
async def test_gitlab_provider_disabled_without_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "provisioning_enabled", True)
    svc = GitHubProvisioningService(
        token="tok",
        org="acme",
        provider_name="gitlab",
        host="",
        client=_client(lambda _r: httpx.Response(201)),
    )
    assert svc.enabled is False
    with pytest.raises(ProvisioningDisabledError):
        await svc.create_repo("x")


@pytest.mark.asyncio
async def test_gitea_provider_creates_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "provisioning_enabled", True)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/orgs/acme/repos"
        return httpx.Response(
            201,
            json={
                "full_name": "acme/newrepo",
                "clone_url": "https://gitea.example.com/acme/newrepo.git",
                "html_url": "https://gitea.example.com/acme/newrepo",
            },
        )

    svc = GitHubProvisioningService(
        token="tok",
        org="acme",
        provider_name="gitea",
        host="gitea.example.com",
        client=_client(handler),
    )
    assert svc.enabled is True
    repo = await svc.create_repo("newrepo", "desc")
    assert repo.full_name == "acme/newrepo"


@pytest.mark.asyncio
async def test_gitea_provider_reuses_already_existing_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "provisioning_enabled", True)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v1/orgs/acme/repos":
            return httpx.Response(409, text="repository already exists")
        if request.url.path == "/api/v1/repos/acme/orphan":
            return httpx.Response(
                200,
                json={
                    "full_name": "acme/orphan",
                    "clone_url": "https://gitea.example.com/acme/orphan.git",
                    "html_url": "https://gitea.example.com/acme/orphan",
                },
            )
        return httpx.Response(404)

    svc = GitHubProvisioningService(
        token="tok",
        org="acme",
        provider_name="gitea",
        host="gitea.example.com",
        client=_client(handler),
    )
    repo = await svc.create_repo("orphan", "desc")
    assert repo.full_name == "acme/orphan"
    assert calls == ["/api/v1/orgs/acme/repos", "/api/v1/repos/acme/orphan"]
