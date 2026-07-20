"""GitHub App integration API — CEO-managed credentials (write-only) plus the
"Select repo" picker's installation/repository listing.

Mirrors ``roboco.api.routes.telegram``'s credentials surface (CEO-only,
write-only — the API never returns the private key, only ``has_credentials``).
The two listing routes back the New Project dialog's "Select repo" button:
list the App's installations, then list one installation's repositories.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.github_app import (
    GitHubAppCredentialsSetRequest,
    GitHubAppCredentialsStatus,
    InstallationRepositoryResponse,
    InstallationResponse,
)
from roboco.security import guard_deco
from roboco.services.github_app_auth import (
    GitHubAppAPIError,
    GitHubAppNotConfiguredError,
    list_installation_repositories,
    list_installations,
)
from roboco.services.github_app_credentials import (
    GitHubAppCredentialsValidationError,
    get_github_app_credentials_service,
)

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="manage the GitHub App integration")


@router.get("/credentials", response_model=GitHubAppCredentialsStatus)
async def get_github_app_credentials(
    db: DbSession, agent: CurrentAgentContext
) -> GitHubAppCredentialsStatus:
    """Whether the App id + private key are stored. Never the key."""
    _require_ceo(agent)
    has_creds = await get_github_app_credentials_service(db).has_credentials()
    return GitHubAppCredentialsStatus(has_credentials=has_creds)


@router.put("/credentials", response_model=GitHubAppCredentialsStatus)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=16384)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.usage_monitor(max_calls=30, window=3600)
async def set_github_app_credentials(
    data: GitHubAppCredentialsSetRequest, db: DbSession, agent: CurrentAgentContext
) -> GitHubAppCredentialsStatus:
    """Set the App id + private key together (PEM paste)."""
    _require_ceo(agent)
    svc = get_github_app_credentials_service(db)
    try:
        has_creds = await svc.set_credentials(
            app_id=data.app_id, private_key=data.private_key
        )
    except GitHubAppCredentialsValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    await db.commit()
    return GitHubAppCredentialsStatus(has_credentials=has_creds)


@router.delete("/credentials", response_model=GitHubAppCredentialsStatus)
async def clear_github_app_credentials(
    db: DbSession, agent: CurrentAgentContext
) -> GitHubAppCredentialsStatus:
    """Clear the App id + private key."""
    _require_ceo(agent)
    has_creds = await get_github_app_credentials_service(db).set_credentials(
        app_id="", private_key=""
    )
    await db.commit()
    return GitHubAppCredentialsStatus(has_credentials=has_creds)


@router.get("/installations", response_model=list[InstallationResponse])
@guard_deco.rate_limit(requests=30, window=60)
async def get_installations(
    db: DbSession, agent: CurrentAgentContext
) -> list[InstallationResponse]:
    """List every installation of the configured App."""
    _require_ceo(agent)
    try:
        installations = await list_installations(db)
    except GitHubAppNotConfiguredError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except GitHubAppAPIError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)
        ) from e
    return [
        InstallationResponse(id=i.id, account_login=i.account_login)
        for i in installations
    ]


@router.get(
    "/installations/{installation_id}/repositories",
    response_model=list[InstallationRepositoryResponse],
)
@guard_deco.rate_limit(requests=30, window=60)
async def get_installation_repositories(
    installation_id: int, db: DbSession, agent: CurrentAgentContext
) -> list[InstallationRepositoryResponse]:
    """List every repository the given installation can access."""
    _require_ceo(agent)
    try:
        repos = await list_installation_repositories(db, installation_id)
    except GitHubAppNotConfiguredError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except GitHubAppAPIError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)
        ) from e
    return [
        InstallationRepositoryResponse(
            full_name=r.full_name, clone_url=r.clone_url, private=r.private
        )
        for r in repos
    ]
