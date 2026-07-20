"""ProjectService.get_decrypted_token{,_by_slug} — the GitHub App installation-
token branch + its fall back to the stored PAT.

An installation-bound project with App credentials mints a token; any minting
failure (App unconfigured, revoked installation, network hiccup) falls back
to the PAT rather than breaking git operations. Mocks the DB boundary (the
project lookup) and the two collaborators (``github_app_credentials``,
``github_app_auth``) directly — no real network/DB involved.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.github_app_auth import GitHubAppAPIError
from roboco.services.project import ProjectService
from roboco.utils.crypto import encrypt_token


def _project(*, installation_id: int | None, pat: str | None) -> MagicMock:
    p = MagicMock()
    p.id = uuid4()
    p.github_installation_id = installation_id
    p.git_token_encrypted = encrypt_token(pat) if pat else None
    return p


def _svc_with_get(project: MagicMock) -> ProjectService:
    svc = ProjectService(MagicMock())
    svc.get = AsyncMock(return_value=project)  # type: ignore[method-assign]
    svc.get_by_slug = AsyncMock(return_value=project)  # type: ignore[method-assign]
    return svc


@pytest.mark.asyncio
async def test_no_installation_uses_stored_pat() -> None:
    svc = _svc_with_get(_project(installation_id=None, pat="ghp_plain"))
    token = await svc.get_decrypted_token(uuid4())
    assert token == "ghp_plain"


@pytest.mark.asyncio
async def test_no_project_returns_none() -> None:
    svc = _svc_with_get(None)  # type: ignore[arg-type]
    assert await svc.get_decrypted_token(uuid4()) is None
    assert await svc.get_decrypted_token_by_slug("nope") is None


@pytest.mark.asyncio
async def test_installation_id_without_app_creds_falls_back_to_pat() -> None:
    svc = _svc_with_get(_project(installation_id=42, pat="ghp_fallback"))
    fake_creds_svc = MagicMock()
    fake_creds_svc.has_credentials = AsyncMock(return_value=False)
    with patch(
        "roboco.services.project.get_github_app_credentials_service",
        return_value=fake_creds_svc,
    ):
        token = await svc.get_decrypted_token(uuid4())
    assert token == "ghp_fallback"


@pytest.mark.asyncio
async def test_installation_id_with_app_creds_mints_token() -> None:
    svc = _svc_with_get(_project(installation_id=42, pat="ghp_unused"))
    fake_creds_svc = MagicMock()
    fake_creds_svc.has_credentials = AsyncMock(return_value=True)
    with (
        patch(
            "roboco.services.project.get_github_app_credentials_service",
            return_value=fake_creds_svc,
        ),
        patch(
            "roboco.services.project.mint_installation_token",
            AsyncMock(return_value="ghs_minted"),
        ),
    ):
        token = await svc.get_decrypted_token(uuid4())
    assert token == "ghs_minted"


@pytest.mark.asyncio
async def test_mint_failure_falls_back_to_pat() -> None:
    svc = _svc_with_get(_project(installation_id=42, pat="ghp_fallback"))
    fake_creds_svc = MagicMock()
    fake_creds_svc.has_credentials = AsyncMock(return_value=True)
    with (
        patch(
            "roboco.services.project.get_github_app_credentials_service",
            return_value=fake_creds_svc,
        ),
        patch(
            "roboco.services.project.mint_installation_token",
            AsyncMock(side_effect=GitHubAppAPIError("revoked")),
        ),
    ):
        token = await svc.get_decrypted_token(uuid4())
    assert token == "ghp_fallback"


@pytest.mark.asyncio
async def test_mint_failure_with_no_pat_returns_none() -> None:
    svc = _svc_with_get(_project(installation_id=42, pat=None))
    fake_creds_svc = MagicMock()
    fake_creds_svc.has_credentials = AsyncMock(return_value=True)
    with (
        patch(
            "roboco.services.project.get_github_app_credentials_service",
            return_value=fake_creds_svc,
        ),
        patch(
            "roboco.services.project.mint_installation_token",
            AsyncMock(side_effect=GitHubAppAPIError("revoked")),
        ),
    ):
        token = await svc.get_decrypted_token(uuid4())
    assert token is None


@pytest.mark.asyncio
async def test_by_slug_mints_token_too() -> None:
    svc = _svc_with_get(_project(installation_id=7, pat=None))
    fake_creds_svc = MagicMock()
    fake_creds_svc.has_credentials = AsyncMock(return_value=True)
    with (
        patch(
            "roboco.services.project.get_github_app_credentials_service",
            return_value=fake_creds_svc,
        ),
        patch(
            "roboco.services.project.mint_installation_token",
            AsyncMock(return_value="ghs_minted"),
        ),
    ):
        token = await svc.get_decrypted_token_by_slug("acme")
    assert token == "ghs_minted"
