"""System settings API — read and update runtime-editable settings.

Backs the panel settings page. Values persist in the ``system_settings`` table
and are read by the backend (e.g. the transcript-retention prune sweep) with a
``roboco.config`` default as the fallback when a key is unset.
"""

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import DbSession
from roboco.api.schemas.settings import (
    FeatureFlag,
    FeatureFlagsResponse,
    SettingsResponse,
    SettingUpdate,
)
from roboco.security import guard_deco
from roboco.services.settings import (
    FEATURE_FLAGS,
    SettingValidationError,
    feature_flag_effective_values,
    get_settings_service,
)

router = APIRouter()


@router.get("", response_model=SettingsResponse)
async def list_settings(db: DbSession) -> SettingsResponse:
    """Return every stored runtime-editable setting."""
    return SettingsResponse(settings=await get_settings_service(db).all())


@router.get("/feature-flags", response_model=FeatureFlagsResponse)
async def get_feature_flags(db: DbSession) -> FeatureFlagsResponse:
    """Effective feature-flag values (stored override, else the env default)."""
    effective = await feature_flag_effective_values(db)
    return FeatureFlagsResponse(
        flags=[
            FeatureFlag(key=key, label=label, enabled=effective.get(key, False))
            for key, label in FEATURE_FLAGS
        ]
    )


@router.put("/{key}", response_model=SettingsResponse)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=8192)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def update_setting(
    key: str, data: SettingUpdate, db: DbSession
) -> SettingsResponse:
    """Validate and persist a single setting, returning the full updated map."""
    service = get_settings_service(db)
    try:
        await service.set(key, data.value)
    except SettingValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    # Write route commits explicitly (get_db auto-commit is unreliable).
    await db.commit()
    return SettingsResponse(settings=await service.all())
