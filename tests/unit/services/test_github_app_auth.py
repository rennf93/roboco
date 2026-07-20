"""GitHub App installation-token minting — JWT shape, token cache, pagination.

The App JWT is signed RS256 from the (mocked) stored credentials; every REST
call goes through a MockTransport-free ``httpx.AsyncClient`` patch mirroring
``test_git_pr_ci_status.py``'s idiom, since the module owns its own client
lifecycle (no injection seam, matching the spec's plain-function shape).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from roboco.services import github_app_auth
from roboco.services.github_app_auth import (
    GitHubAppAPIError,
    GitHubAppNotConfiguredError,
    list_installation_repositories,
    list_installations,
    mint_installation_token,
)
from roboco.services.github_app_credentials import GitHubAppCredentialsData


def _generate_rsa_keypair() -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


_PRIVATE_KEY_PEM = _generate_rsa_keypair()
_APP_ID = "998877"
_SECOND_CALL_COUNT = 2


@pytest.fixture(autouse=True)
def _clear_token_cache() -> None:
    github_app_auth._token_cache.clear()


def _patch_creds(*, configured: bool = True) -> Any:
    fake_service = MagicMock()
    creds = (
        GitHubAppCredentialsData(app_id=_APP_ID, private_key=_PRIVATE_KEY_PEM)
        if configured
        else None
    )
    fake_service.get_decrypted = AsyncMock(return_value=creds)
    return patch(
        "roboco.services.github_app_auth.get_github_app_credentials_service",
        return_value=fake_service,
    )


def _client(
    *, get: list[MagicMock] | None = None, post: list[MagicMock] | None = None
) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if get is not None:
        client.get = AsyncMock(side_effect=get)
    if post is not None:
        client.post = AsyncMock(side_effect=post)
    return client


def _resp(status_code: int, json_payload: Any = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_payload
    resp.text = text
    return resp


def _token_resp(token: str, *, expires_in_seconds: int = 3600) -> MagicMock:
    expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in_seconds)).isoformat()
    return _resp(201, {"token": token, "expires_at": expires_at})


@pytest.mark.asyncio
async def test_missing_credentials_raises_not_configured() -> None:
    with _patch_creds(configured=False), pytest.raises(GitHubAppNotConfiguredError):
        await mint_installation_token(MagicMock(), 42)


@pytest.mark.asyncio
async def test_jwt_claims_shape() -> None:
    """The App JWT sent as the POST Authorization header decodes to
    iss=app_id, an iat backdated ~60s, and an exp within the 9-minute TTL."""
    client = _client(post=[_token_resp("tok-1")])
    with (
        _patch_creds(),
        patch("roboco.services.github_app_auth.httpx.AsyncClient", return_value=client),
    ):
        before = int(time.time())
        await mint_installation_token(MagicMock(), 1)

    _, kwargs = client.post.call_args
    auth_header = kwargs["headers"]["Authorization"]
    assert auth_header.startswith("Bearer ")
    app_jwt = auth_header.removeprefix("Bearer ")

    claims = jwt.decode(app_jwt, options={"verify_signature": False})
    assert claims["iss"] == _APP_ID
    assert before - claims["iat"] == pytest.approx(60, abs=2)
    assert claims["exp"] - claims["iat"] == pytest.approx(9 * 60 + 60, abs=2)


@pytest.mark.asyncio
async def test_cache_reuse_skips_a_second_mint() -> None:
    client = _client(post=[_token_resp("tok-cached", expires_in_seconds=3600)])
    with (
        _patch_creds(),
        patch("roboco.services.github_app_auth.httpx.AsyncClient", return_value=client),
    ):
        first = await mint_installation_token(MagicMock(), 7)
        second = await mint_installation_token(MagicMock(), 7)

    assert first == second == "tok-cached"
    assert client.post.call_count == 1


@pytest.mark.asyncio
async def test_expiry_re_mints_past_the_refresh_margin() -> None:
    # Expires in 30s — under the 5-minute refresh margin, so the second call
    # must not trust the cached entry.
    client = _client(
        post=[
            _token_resp("tok-old", expires_in_seconds=30),
            _token_resp("tok-fresh", expires_in_seconds=3600),
        ]
    )
    with (
        _patch_creds(),
        patch("roboco.services.github_app_auth.httpx.AsyncClient", return_value=client),
    ):
        first = await mint_installation_token(MagicMock(), 9)
        second = await mint_installation_token(MagicMock(), 9)

    assert first == "tok-old"
    assert second == "tok-fresh"
    assert client.post.call_count == _SECOND_CALL_COUNT


@pytest.mark.asyncio
async def test_mint_failure_raises_api_error() -> None:
    client = _client(post=[_resp(401, text="Bad credentials")])
    with (
        _patch_creds(),
        patch("roboco.services.github_app_auth.httpx.AsyncClient", return_value=client),
        pytest.raises(GitHubAppAPIError),
    ):
        await mint_installation_token(MagicMock(), 5)


@pytest.mark.asyncio
async def test_list_installations_maps_account_login() -> None:
    payload = [
        {"id": 1, "account": {"login": "acme"}},
        {"id": 2, "account": {"login": "widgets"}},
    ]
    client = _client(get=[_resp(200, payload)])
    with (
        _patch_creds(),
        patch("roboco.services.github_app_auth.httpx.AsyncClient", return_value=client),
    ):
        installations = await list_installations(MagicMock())

    assert [(i.id, i.account_login) for i in installations] == [
        (1, "acme"),
        (2, "widgets"),
    ]


@pytest.mark.asyncio
async def test_list_installation_repositories_paginates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(github_app_auth, "_PER_PAGE", 2)

    def _repo(name: str) -> dict[str, Any]:
        return {
            "full_name": f"acme/{name}",
            "clone_url": f"https://github.com/acme/{name}.git",
            "private": True,
        }

    page1 = _resp(200, {"repositories": [_repo("a"), _repo("b")]})
    page2 = _resp(200, {"repositories": [_repo("c")]})
    client = _client(post=[_token_resp("tok")], get=[page1, page2])
    with (
        _patch_creds(),
        patch("roboco.services.github_app_auth.httpx.AsyncClient", return_value=client),
    ):
        repos = await list_installation_repositories(MagicMock(), 11)

    assert [r.full_name for r in repos] == ["acme/a", "acme/b", "acme/c"]
    assert client.get.call_count == _SECOND_CALL_COUNT
