"""
Providers API Schemas

Minimal surface that backs the Settings UI:
 - fetch the preset catalog of selectable models
 - set / clear / check the single Ollama Cloud API key
 - read current routing assignments (so the UI renders Mix mode)
 - apply a routing mode (anthropic | ollama | mix)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import UUID  # noqa: TC003  (pydantic needs the type at runtime)

from pydantic import BaseModel, Field

from roboco.models.base import AssignmentScope, ModelProvider  # noqa: TC001
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from roboco.db.tables import ModelAssignmentTable


# =============================================================================
# CATALOG
# =============================================================================


class CatalogEntryResponse(BaseModel):
    """One selectable model in the Settings dropdown."""

    model_name: str
    provider_type: ModelProvider
    display_name: str


# =============================================================================
# OLLAMA API KEY
# =============================================================================


class OllamaKeyStatus(BaseModel):
    """Whether the Ollama Cloud provider has a stored token."""

    has_key: bool
    enabled: bool


class SetOllamaKeyRequest(BaseModel):
    """Set or clear the Ollama Cloud API key.

    Pass an empty string to clear. Pass a non-empty string to save
    (encrypted with Fernet) and mark the Ollama provider enabled.
    """

    api_key: str = Field(default="")


# =============================================================================
# MODEL ASSIGNMENTS (read-only for the UI)
# =============================================================================


class AssignmentResponse(BaseModel):
    """Routing rule with the provider summary flattened for UI rendering."""

    id: UUID
    scope: AssignmentScope
    scope_value: str | None
    provider_type: ModelProvider
    model_name: str


def assignment_to_response(
    row: ModelAssignmentTable,
) -> AssignmentResponse:
    """Convert a ModelAssignmentTable row + joined provider to a response."""
    return AssignmentResponse(
        id=require_uuid(row.id),
        scope=row.scope,
        scope_value=row.scope_value,
        provider_type=row.provider.type,
        model_name=row.model_name,
    )


# =============================================================================
# MODE APPLY
# =============================================================================


class ApplyModeRequest(BaseModel):
    """Apply a routing mode in one atomic call.

    - mode="anthropic": clear every assignment; spawns fall through to
      ROLE_MODEL_MAP + mounted ~/.claude.
    - mode="ollama": clear every assignment; set GLOBAL default to
      `default_model` (if omitted, the service picks a sensible default).
    - mode="mix": clear existing per-agent pins; upsert the `per_agent`
      map verbatim. Role + GLOBAL rows are left untouched so the user can
      layer with an existing partial setup.
    """

    mode: Literal["anthropic", "ollama", "mix"]
    default_model: str | None = None
    per_agent: dict[str, str] | None = None


class ModeResponse(BaseModel):
    """Server-side view of the current mode + a snapshot of active rules."""

    mode: Literal["anthropic", "ollama", "mix"]
    assignments: list[AssignmentResponse]
