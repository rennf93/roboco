"""System settings API — read and update runtime-editable settings.

Backs the panel settings page. Values persist in the ``system_settings`` table
and are read by the backend (e.g. the transcript-retention prune sweep) with a
``roboco.config`` default as the fallback when a key is unset.
"""

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import DbSession
from roboco.api.schemas.settings import SettingsResponse, SettingUpdate
from roboco.services.settings import SettingValidationError, get_settings_service

router = APIRouter()


@router.get("", response_model=SettingsResponse)
async def list_settings(db: DbSession) -> SettingsResponse:
    """Return every stored runtime-editable setting."""
    return SettingsResponse(settings=await get_settings_service(db).all())


@router.put("/{key}", response_model=SettingsResponse)
async def update_setting(
    key: str, data: SettingUpdate, db: DbSession
) -> SettingsResponse:
    """Validate and persist a single setting, returning the full updated map."""
    service = get_settings_service(db)
    try:
        await service.set(key, data.value)
    except SettingValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    # Write route commits explicitly (get_db auto-commit is unreliable).
    await db.commit()
    return SettingsResponse(settings=await service.all())
