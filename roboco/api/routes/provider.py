"""
Provider Routes

Thin HTTP plumbing for the Settings UI's AI-routing panel. Endpoints
cover the whole UX: fetch the catalog, get / set the Ollama key,
configure / test / discover the self-hosted server, fetch the current
mode + assignments, apply a mode change. No provider CRUD — the
providers (Anthropic, Ollama Cloud, Self-Hosted) are pre-seeded by
migrations 004 and 028.
"""

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession, require_pm_or_above
from roboco.api.schemas.provider import (
    ApplyModeRequest,
    CatalogEntryResponse,
    ModeResponse,
    OllamaKeyStatus,
    SelfHostedConfigRequest,
    SelfHostedConfigResponse,
    SelfHostedModelEntry,
    SelfHostedTestResponse,
    SetOllamaKeyRequest,
    assignment_to_response,
)
from roboco.models.base import ModelProvider
from roboco.models.llm_catalog import MODEL_CATALOG
from roboco.services.base import NotFoundError
from roboco.services.llm import get_model_routing_service, probe_ollama_tags
from roboco.services.provider import ProviderUpdate, get_provider_service
from roboco.utils.converters import require_uuid

router = APIRouter()


# =============================================================================
# CATALOG
# =============================================================================


@router.get("/catalog", response_model=list[CatalogEntryResponse])
async def get_catalog(
    agent: CurrentAgentContext,
) -> list[CatalogEntryResponse]:
    """Return the preset list of selectable models.

    Order matches display order in the UI. Static — served from constants
    so the UI never needs to hit the DB for the model dropdown.
    """
    require_pm_or_above(agent.role, "view the model catalog")
    return [
        CatalogEntryResponse(
            model_name=entry.model_name,
            provider_type=entry.provider_type,
            display_name=entry.display_name,
        )
        for entry in MODEL_CATALOG
    ]


# =============================================================================
# OLLAMA API KEY (the one and only secret the user types)
# =============================================================================


@router.get("/ollama-key", response_model=OllamaKeyStatus)
async def get_ollama_key_status(
    db: DbSession,
    agent: CurrentAgentContext,
) -> OllamaKeyStatus:
    """Return whether the Ollama Cloud key is set + enabled."""
    require_pm_or_above(agent.role, "view the Ollama key status")
    provider_svc = get_provider_service(db)
    providers = await provider_svc.list_providers(include_disabled=True)
    ollama = next(
        (p for p in providers if p.type == ModelProvider.OLLAMA_CLOUD),
        None,
    )
    if ollama is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=("Ollama Cloud provider not seeded. Run alembic upgrade head."),
        )
    return OllamaKeyStatus(
        has_key=bool(ollama.auth_token_encrypted),
        enabled=ollama.enabled,
    )


@router.put("/ollama-key", response_model=OllamaKeyStatus)
async def set_ollama_key(
    data: SetOllamaKeyRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> OllamaKeyStatus:
    """Set or clear the Ollama Cloud API key.

    Empty string → clears and disables the provider. Any other value →
    Fernet-encrypts + marks enabled. This is the only secret the user
    types anywhere.
    """
    require_pm_or_above(agent.role, "set the Ollama key")
    routing = get_model_routing_service(db)
    try:
        provider = await routing.set_ollama_api_key(data.api_key)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await db.commit()
    return OllamaKeyStatus(
        has_key=bool(provider.auth_token_encrypted),
        enabled=provider.enabled,
    )


# =============================================================================
# SELF-HOSTED (LOCAL) OLLAMA SERVER
# =============================================================================


@router.get("/self-hosted", response_model=SelfHostedConfigResponse)
async def get_self_hosted_config(
    db: DbSession,
    agent: CurrentAgentContext,
) -> SelfHostedConfigResponse:
    """Return the current configuration of the LOCAL (self-hosted) provider.

    The LOCAL provider row must be seeded (migration 028). Returns
    ``{base_url, has_token, enabled}`` so the Settings UI can display
    the current state without exposing the encrypted token.
    """
    require_pm_or_above(agent.role, "view the self-hosted provider config")
    provider_svc = get_provider_service(db)
    providers = await provider_svc.list_providers(include_disabled=True)
    local = next(
        (p for p in providers if p.type == ModelProvider.LOCAL),
        None,
    )
    if local is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Self-Hosted provider not seeded. "
                "Run alembic upgrade head (migration 028)."
            ),
        )
    return SelfHostedConfigResponse(
        base_url=local.base_url,
        has_token=bool(local.auth_token_encrypted),
        enabled=local.enabled,
    )


@router.put("/self-hosted", response_model=SelfHostedConfigResponse)
async def set_self_hosted_config(
    data: SelfHostedConfigRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> SelfHostedConfigResponse:
    """Save the base URL (and optionally an auth token) for the LOCAL provider.

    The LOCAL provider row must be seeded (migration 028). The token, when
    provided and non-empty, is Fernet-encrypted before storing. An empty
    string for `auth_token` clears the stored token. The provider is
    automatically enabled only when a non-empty base_url is provided.
    """
    require_pm_or_above(agent.role, "configure the self-hosted provider")
    provider_svc = get_provider_service(db)
    providers = await provider_svc.list_providers(include_disabled=True)
    local = next(
        (p for p in providers if p.type == ModelProvider.LOCAL),
        None,
    )
    if local is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Self-Hosted provider not seeded. "
                "Run alembic upgrade head (migration 028)."
            ),
        )

    # Determine token update intent:
    # None → leave unchanged, "" → clear, non-empty str → re-encrypt.
    clear_token = data.auth_token is not None and data.auth_token == ""
    new_token = data.auth_token if (data.auth_token and data.auth_token != "") else None

    await provider_svc.update_provider(
        require_uuid(local.id),
        ProviderUpdate(
            base_url=data.base_url,
            auth_token=new_token,
            clear_auth_token=clear_token,
            # Only enable the provider when a non-empty base_url is provided.
            enabled=bool(data.base_url),
        ),
    )
    await db.commit()

    # Re-fetch after commit to get the persisted state.
    updated = await provider_svc.list_providers(include_disabled=True)
    local_updated = next(p for p in updated if p.type == ModelProvider.LOCAL)
    return SelfHostedConfigResponse(
        base_url=local_updated.base_url,
        has_token=bool(local_updated.auth_token_encrypted),
        enabled=local_updated.enabled,
    )


@router.post("/self-hosted/test", response_model=SelfHostedTestResponse)
async def test_self_hosted_connection(
    db: DbSession,
    agent: CurrentAgentContext,
) -> SelfHostedTestResponse:
    """Probe the configured self-hosted Ollama server.

    Returns ``{ok: true, model_count: N}`` when the server is reachable
    and returns a valid model list from ``{base_url}/api/tags``.
    Returns ``{ok: false, error: '<message>'}`` on any failure — never
    raises a 500, so the Settings UI can display a friendly error.
    """
    require_pm_or_above(agent.role, "test the self-hosted connection")
    provider_svc = get_provider_service(db)
    providers = await provider_svc.list_providers(include_disabled=True)
    local = next(
        (p for p in providers if p.type == ModelProvider.LOCAL),
        None,
    )
    if local is None or not local.base_url:
        return SelfHostedTestResponse(
            ok=False,
            error=(
                "Self-hosted server is not configured."
                " Set base_url first via PUT /self-hosted."
            ),
        )

    models, error = await probe_ollama_tags(local.base_url)
    if error is not None:
        return SelfHostedTestResponse(ok=False, error=error)
    return SelfHostedTestResponse(ok=True, model_count=len(models))


@router.get("/self-hosted/models", response_model=list[SelfHostedModelEntry])
async def get_self_hosted_models(
    db: DbSession,
    agent: CurrentAgentContext,
) -> list[SelfHostedModelEntry]:
    """Return the list of models available on the self-hosted Ollama server.

    Queries ``{base_url}/api/tags`` and returns ``[{model_name, display_name}]``
    for each model entry. Raises 503 if the server is unreachable, 404 if
    the LOCAL provider is not configured.
    """
    require_pm_or_above(agent.role, "list self-hosted models")
    provider_svc = get_provider_service(db)
    providers = await provider_svc.list_providers(include_disabled=True)
    local = next(
        (p for p in providers if p.type == ModelProvider.LOCAL),
        None,
    )
    if local is None or not local.base_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Self-hosted provider is not configured. "
                "Set base_url via PUT /self-hosted first."
            ),
        )

    model_names, error = await probe_ollama_tags(local.base_url)
    if error is not None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Self-hosted server unreachable: {error}",
        )
    return [
        SelfHostedModelEntry(model_name=name, display_name=name) for name in model_names
    ]


# =============================================================================
# MODE (the three-way toggle)
# =============================================================================


@router.get("", response_model=ModeResponse)
async def get_current_mode(
    db: DbSession,
    agent: CurrentAgentContext,
) -> ModeResponse:
    """Return the current mode + all live assignments for UI rendering."""
    require_pm_or_above(agent.role, "view routing state")
    routing = get_model_routing_service(db)
    mode = await routing.derive_mode()
    assignments = await routing.list_assignments()
    return ModeResponse(
        mode=mode,
        assignments=[assignment_to_response(a) for a in assignments],
    )


@router.post("", response_model=ModeResponse)
async def apply_mode(
    data: ApplyModeRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ModeResponse:
    """Apply a mode change atomically.

    Returns the new assignment snapshot so the UI can re-render without a
    second round-trip.
    """
    require_pm_or_above(agent.role, "change routing mode")
    routing = get_model_routing_service(db)
    try:
        await routing.apply_mode(
            mode=data.mode,
            default_model=data.default_model,
            per_agent=data.per_agent,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await db.commit()

    mode = await routing.derive_mode()
    assignments = await routing.list_assignments()
    return ModeResponse(
        mode=mode,
        assignments=[assignment_to_response(a) for a in assignments],
    )
