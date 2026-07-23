"""
Provider Routes

Thin HTTP plumbing for the Settings UI's AI-routing panel. Endpoints
cover the whole UX: fetch the catalog, get / set the Ollama key,
configure / test / discover the self-hosted server, fetch the current
mode + assignments, apply a mode change. No provider CRUD — the
providers (Anthropic, Ollama Cloud, Self-Hosted) are pre-seeded by
migrations 004 and 028.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession, require_pm_or_above
from roboco.api.schemas.provider import (
    ApplyModeRequest,
    CatalogEntryResponse,
    ComplexityOverrideRequest,
    ComplexityOverrideResponse,
    GrokKeyStatus,
    ModeResponse,
    OllamaKeyStatus,
    RoutingPresetApplyResponse,
    RoutingPresetSummary,
    SaveRoutingPresetRequest,
    SelfHostedConfigRequest,
    SelfHostedConfigResponse,
    SelfHostedModelEntry,
    SelfHostedTestResponse,
    SetGrokKeyRequest,
    SetOllamaKeyRequest,
    assignment_to_response,
    routing_preset_to_summary,
)
from roboco.billing.pricing import input_price_per_million
from roboco.models.base import AssignmentScope, ModelProvider
from roboco.models.llm_catalog import MODEL_CATALOG
from roboco.models.runtime import ROLE_MODEL_MAP
from roboco.security import guard_deco
from roboco.services.base import NotFoundError
from roboco.services.llm import get_model_routing_service, probe_ollama_tags
from roboco.services.provider import ProviderUpdate, get_provider_service
from roboco.utils.converters import require_uuid

router = APIRouter()

# Roles the complexity-override endpoint accepts a row for. Coordinator roles
# (cell_pm, main_pm — CLAUDE.md's own _COORDINATOR_ROLES) and pr_reviewer/
# board/CEO-facing roles are never offered a row — tier pinning for those is
# deliberate (see set_complexity_override). cell_pm is deliberately excluded
# even though it's not board/CEO-facing: the org's documented weak-model
# incidents were precisely a cheap model landed on a PM/coordinator role,
# not a leaf developer — a coordinator is the last place to gamble a
# downgrade on.
_COMPLEXITY_OVERRIDE_ROLES: frozenset[str] = frozenset(
    {"developer", "qa", "documenter"}
)

# Human remediation hint per provider type, for a complexity override that
# resolves to a not-ready (disabled / unconfigured) provider.
_PROVIDER_REMEDIATION: dict[ModelProvider, str] = {
    ModelProvider.GROK: "Save the Grok (xAI) API key first (PUT /providers/grok-key).",
    ModelProvider.OLLAMA_CLOUD: (
        "Save an Ollama Cloud API key first (PUT /providers/ollama-key)."
    ),
    ModelProvider.LOCAL: (
        "Configure + test the self-hosted server first (PUT /providers/self-hosted)."
    ),
    ModelProvider.ANTHROPIC: "The Anthropic provider is disabled — re-enable it first.",
    ModelProvider.OPENAI: (
        "Codex authenticates via a mounted ChatGPT-subscription ~/.codex "
        "directory, not a key — enable it via the Codex mode button, or "
        "assign a Codex model to an agent in Mix mode (both force-enable "
        "the row)."
    ),
    ModelProvider.GEMINI: (
        "Gemini authenticates via a mounted OAuth ~/.gemini credential, not "
        "a key — enable it via the Gemini mode button, or assign a Gemini "
        "model to an agent in Mix mode (both force-enable the row)."
    ),
}


def _provider_remediation(provider_type: ModelProvider) -> str:
    return _PROVIDER_REMEDIATION.get(
        provider_type, f"The {provider_type.value} provider is not configured."
    )


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
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=8192)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.usage_monitor(max_calls=30, window=3600)
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
# GROK (xAI) API KEY
# =============================================================================


@router.get("/grok-key", response_model=GrokKeyStatus)
async def get_grok_key_status(
    db: DbSession,
    agent: CurrentAgentContext,
) -> GrokKeyStatus:
    """Return whether the Grok (xAI) key is set + enabled."""
    require_pm_or_above(agent.role, "view the Grok key status")
    provider_svc = get_provider_service(db)
    providers = await provider_svc.list_providers(include_disabled=True)
    grok = next((p for p in providers if p.type == ModelProvider.GROK), None)
    if grok is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grok provider not seeded. Run alembic upgrade head.",
        )
    return GrokKeyStatus(
        has_key=bool(grok.auth_token_encrypted),
        enabled=grok.enabled,
    )


@router.put("/grok-key", response_model=GrokKeyStatus)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=8192)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.usage_monitor(max_calls=30, window=3600)
async def set_grok_key(
    data: SetGrokKeyRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GrokKeyStatus:
    """Set or clear the Grok (xAI) API key.

    Empty string → clears and disables the provider. Any other value →
    Fernet-encrypts + marks enabled. Used against https://api.x.ai/v1.
    """
    require_pm_or_above(agent.role, "set the Grok key")
    routing = get_model_routing_service(db)
    try:
        provider = await routing.set_grok_api_key(data.api_key)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await db.commit()
    return GrokKeyStatus(
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
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=8192)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.usage_monitor(max_calls=30, window=3600)
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
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
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


# =============================================================================
# COMPLEXITY OVERRIDES (cost-tiered routing: compound ROLE(":"complexity) rows)
# =============================================================================


def _parse_complexity_override(
    scope_value: str, model_name: str
) -> ComplexityOverrideResponse | None:
    """Parse a ROLE scope_value into a response row, or None if not a
    well-formed "role:low"/"role:high" compound key (a plain role row, or a
    malformed compound value, are both silently skipped)."""
    role, sep, complexity = scope_value.partition(":")
    if not sep or not role:
        return None
    if complexity == "low":
        return ComplexityOverrideResponse(
            role=role, complexity="low", model_name=model_name
        )
    if complexity == "high":
        return ComplexityOverrideResponse(
            role=role, complexity="high", model_name=model_name
        )
    return None


@router.get("/complexity-overrides", response_model=list[ComplexityOverrideResponse])
async def get_complexity_overrides(
    db: DbSession,
    agent: CurrentAgentContext,
) -> list[ComplexityOverrideResponse]:
    """List the active compound ROLE(":"complexity) cost-tiered rows."""
    require_pm_or_above(agent.role, "view complexity overrides")
    routing = get_model_routing_service(db)
    rows = await routing.list_assignments()
    overrides = []
    for row in rows:
        if row.scope != AssignmentScope.ROLE or not row.scope_value:
            continue
        parsed = _parse_complexity_override(row.scope_value, row.model_name)
        if parsed is not None:
            overrides.append(parsed)
    return overrides


@router.put("/complexity-overrides", response_model=ComplexityOverrideResponse)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=4096)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def set_complexity_override(
    data: ComplexityOverrideRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ComplexityOverrideResponse:
    """Upsert one ROLE(":"complexity) cost-tiered override.

    Write-time guards enforce the policy (never at read/resolve time):
      - role allowlist: only {developer, qa, documenter} are offered a row —
        cell_pm/main_pm (coordinators), pr_reviewer, and board/CEO-facing
        roles are rejected outright, tier pinning for those is deliberate.
      - downgrade-only: `model_name` must not be a costlier tier than the
        role's `ROLE_MODEL_MAP` baseline, compared via each model's
        per-1M-token input price (`billing.pricing.input_price_per_million`)
        since the model catalog carries no explicit tier ordering.
      - provider readiness: `model_name` must resolve to a provider that is
        both known (catalog or LOCAL self-hosted) AND enabled+configured —
        an override to a disabled/unconfigured provider would otherwise
        silently no-op at spawn (falling back to the legacy Anthropic path)
        behind a success toast; mirrors the intent of the Mix section's
        client-side needsGrok/needsKey/needsSelfHosted guards, enforced here
        server-side where it can't be bypassed by a direct API call.

    A model that resolves to a different provider FAMILY than the role's
    Anthropic baseline (e.g. an Anthropic role pinned to Grok/Ollama/
    self-hosted) is still ALLOWED — the CEO may want that deliberately — but
    the response carries a non-null `warning` so it's never silent.
    """
    require_pm_or_above(agent.role, "change complexity-based routing overrides")
    if data.role not in _COMPLEXITY_OVERRIDE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{data.role}' is not eligible for a complexity override — "
                "tier pinning for coordinator/board/CEO-facing roles is "
                "deliberate."
            ),
        )
    baseline_model = ROLE_MODEL_MAP.get(data.role, "sonnet")
    if input_price_per_million(data.model_name) > input_price_per_million(
        baseline_model
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{data.model_name}' is a costlier tier than {data.role}'s "
                f"baseline ('{baseline_model}') — complexity overrides are "
                "downgrade-only."
            ),
        )
    routing = get_model_routing_service(db)
    provider = await routing.resolve_provider_for_model(data.model_name)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown model '{data.model_name}'. Use one from "
                "GET /api/providers/catalog."
            ),
        )
    if not provider.enabled or (
        provider.type == ModelProvider.LOCAL and not provider.base_url
    ):
        remediation = _provider_remediation(provider.type)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{data.model_name}' routes through {provider.type.value}, "
                "which isn't configured yet — it would silently fall back to "
                f"the legacy Anthropic path at spawn. {remediation}"
            ),
        )
    warning = (
        f"'{data.model_name}' routes through {provider.type.value}, a "
        f"different provider family than {data.role}'s Anthropic baseline "
        f"('{baseline_model}') — allowed, but make sure that's deliberate."
        if provider.type != ModelProvider.ANTHROPIC
        else None
    )
    try:
        row = await routing.upsert_assignment(
            scope=AssignmentScope.ROLE,
            scope_value=f"{data.role}:{data.complexity}",
            model_name=data.model_name,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    await db.commit()
    return ComplexityOverrideResponse(
        role=data.role,
        complexity=data.complexity,
        model_name=row.model_name,
        warning=warning,
    )


@router.delete(
    "/complexity-overrides/{role}/{complexity}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_complexity_override(
    role: str,
    complexity: Literal["low", "high"],
    db: DbSession,
    agent: CurrentAgentContext,
) -> None:
    """Remove one ROLE(":"complexity) cost-tiered override row."""
    require_pm_or_above(agent.role, "remove a complexity override")
    routing = get_model_routing_service(db)
    try:
        await routing.delete_assignment(
            scope=AssignmentScope.ROLE, scope_value=f"{role}:{complexity}"
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await db.commit()


# =============================================================================
# ROUTING PRESETS (named, full snapshots of the routing state)
# =============================================================================


@router.get("/presets", response_model=list[RoutingPresetSummary])
async def list_routing_presets(
    db: DbSession,
    agent: CurrentAgentContext,
) -> list[RoutingPresetSummary]:
    """List saved routing presets, newest first (no payloads — see
    `POST /presets/{id}/apply` to inspect one by applying it)."""
    require_pm_or_above(agent.role, "view routing presets")
    routing = get_model_routing_service(db)
    rows = await routing.list_routing_presets()
    return [routing_preset_to_summary(r) for r in rows]


@router.post("/presets", response_model=RoutingPresetSummary)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=4096)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def save_routing_preset(
    data: SaveRoutingPresetRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> RoutingPresetSummary:
    """Snapshot the CURRENT routing state under `data.name`.

    Same privilege level as the mode-apply / mix-save endpoints. A duplicate
    name is a 409 (the panel names presets, so a collision is a UI mistake,
    not routine traffic).
    """
    require_pm_or_above(agent.role, "save a routing preset")
    routing = get_model_routing_service(db)
    try:
        row = await routing.save_routing_preset(data.name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    await db.commit()
    return routing_preset_to_summary(row)


@router.post("/presets/{preset_id}/apply", response_model=RoutingPresetApplyResponse)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def apply_routing_preset(
    preset_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> RoutingPresetApplyResponse:
    """Replace the current routing state with the preset's snapshot.

    Transactional: the delete + re-upserts of the new rows happen in this
    request's session, committed once at the end — a mid-apply failure never
    leaves a half-swapped state. A row referencing a model no longer in the
    catalog (or any other now-invalid entry) is skipped and reported in
    `skipped`, never silently dropped and never failing the rows that DID
    validate.
    """
    require_pm_or_above(agent.role, "apply a routing preset")
    routing = get_model_routing_service(db)
    try:
        skipped = await routing.apply_routing_preset(preset_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await db.commit()

    mode = await routing.derive_mode()
    assignments = await routing.list_assignments()
    return RoutingPresetApplyResponse(
        mode=mode,
        assignments=[assignment_to_response(a) for a in assignments],
        skipped=skipped,
    )


@router.delete("/presets/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routing_preset(
    preset_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> None:
    """Delete a saved routing preset."""
    require_pm_or_above(agent.role, "delete a routing preset")
    routing = get_model_routing_service(db)
    try:
        await routing.delete_routing_preset(preset_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await db.commit()
