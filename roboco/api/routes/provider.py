"""
Provider Routes

Thin HTTP plumbing for the Settings UI's AI-routing panel. Four endpoints
cover the whole UX: fetch the catalog, get / set the Ollama key, fetch the
current mode + assignments, apply a mode change. No provider CRUD — the
two providers (Anthropic + Ollama Cloud) are pre-seeded by migration 004.
"""

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession, require_pm_or_above
from roboco.api.schemas.provider import (
    ApplyModeRequest,
    CatalogEntryResponse,
    ModeResponse,
    OllamaKeyStatus,
    SetOllamaKeyRequest,
    assignment_to_response,
)
from roboco.models.base import ModelProvider
from roboco.models.llm_catalog import MODEL_CATALOG
from roboco.services.base import NotFoundError
from roboco.services.llm import get_model_routing_service
from roboco.services.provider import get_provider_service

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
        mode=mode,  # type: ignore[arg-type]
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
        mode=mode,  # type: ignore[arg-type]
        assignments=[assignment_to_response(a) for a in assignments],
    )
