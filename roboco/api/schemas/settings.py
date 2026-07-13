"""Schemas for the system-settings API."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class SettingUpdate(BaseModel):
    """Body for PUT /settings/{key} — the new value (stored as text)."""

    value: str = Field(..., description="New value for the setting, stored as text")

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_scalars(cls, v: object) -> object:
        """Accept the JSON scalars the panel naturally sends.

        The feature-flags card sends booleans and numeric settings send
        numbers; values persist as text, so coerce to the stored form
        (bools to the 'true'/'false' the per-key validators parse) instead
        of 422ing on type alone.
        """
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int | float):
            return str(v)
        return v


class SettingsResponse(BaseModel):
    """All runtime-editable settings as a flat key→value map."""

    settings: dict[str, str] = Field(default_factory=dict)


class FeatureFlag(BaseModel):
    """One panel-tunable feature flag and its current effective value."""

    key: str
    label: str
    enabled: bool


class FeatureFlagsResponse(BaseModel):
    """Effective feature-flag values for the Settings panel (override, else env)."""

    flags: list[FeatureFlag] = Field(default_factory=list)
    note: str = "Changes take effect on the next backend restart."
