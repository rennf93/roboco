"""
Providers API Schemas

Minimal surface that backs the Settings UI:
 - fetch the preset catalog of selectable models
 - set / clear / check the single Ollama Cloud API key
 - configure / test / discover the self-hosted (LOCAL) Ollama server
 - read current routing assignments (so the UI renders Mix mode)
 - apply a routing mode (anthropic | grok | ollama | mix | self_hosted | cost_tiered)
 - read/write/delete cost-tiered complexity overrides (compound ROLE rows)
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  (pydantic needs the type at runtime)
from typing import TYPE_CHECKING, Literal
from uuid import UUID  # noqa: TC003  (pydantic needs the type at runtime)

from pydantic import BaseModel, Field

from roboco.models.base import AssignmentScope, ModelProvider  # noqa: TC001
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from roboco.db.tables import ModelAssignmentTable, RoutingPresetTable


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
# GROK (xAI) API KEY
# =============================================================================


class GrokKeyStatus(BaseModel):
    """Whether the Grok (xAI) provider has a stored key."""

    has_key: bool
    enabled: bool


class SetGrokKeyRequest(BaseModel):
    """Set or clear the Grok (xAI) API key.

    Pass an empty string to clear. Pass a non-empty string to save
    (encrypted with Fernet) and mark the Grok provider enabled. This is the
    standard xAI key used against https://api.x.ai/v1.
    """

    api_key: str = Field(default="")


# =============================================================================
# SELF-HOSTED (LOCAL) OLLAMA SERVER
# =============================================================================


class SelfHostedConfigRequest(BaseModel):
    """Save the base URL (and optionally an auth token) for the self-hosted server.

    `base_url` is the root URL of the Ollama instance, e.g.
    ``http://192.168.1.50:11434``. The Settings UI sends this on every
    save; the service stores it on the LOCAL provider row.

    `auth_token`, when present and non-empty, is Fernet-encrypted before
    storing. Pass ``None`` or omit to leave any existing token unchanged.
    Pass an empty string to clear the stored token.
    """

    base_url: str = Field(..., description="Root URL of the self-hosted Ollama server")
    auth_token: str | None = Field(
        default=None,
        description=(
            "Optional bearer token for the Ollama server; omit to leave unchanged"
        ),
    )


class SelfHostedConfigResponse(BaseModel):
    """Current configuration state of the LOCAL provider row."""

    base_url: str | None
    has_token: bool
    enabled: bool


class SelfHostedTestResponse(BaseModel):
    """Result of a connectivity probe to the self-hosted server.

    The endpoint always returns HTTP 200; reachability is indicated by
    the `ok` field so the UI can display a human-readable error without
    triggering its generic error handler.
    """

    ok: bool
    model_count: int | None = None
    error: str | None = None


class SelfHostedModelEntry(BaseModel):
    """One model available on the self-hosted Ollama server.

    `model_name` is the raw Ollama tag identifier (e.g. ``llama3.1:8b``).
    `display_name` is a human-readable label for the Settings UI dropdown;
    for self-hosted models it mirrors `model_name` since Ollama's ``/api/tags``
    does not return a separate display label.
    """

    model_name: str
    display_name: str


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
      layer with an existing partial setup. Self-hosted model names in
      `per_agent` are routed to the LOCAL provider automatically.
    - mode="self_hosted": clear every assignment; enable LOCAL provider;
      set GLOBAL default to `default_model` (a self-hosted model name).
    - mode="cost_tiered": seed the day-1 cost-tiered compound ROLE(":"complexity)
      rows (see `ModelRoutingService._COST_TIERED_SEED`). Unlike every mode
      above, nothing is cleared first — purely additive on top of whatever
      routing already exists.
    """

    mode: Literal["anthropic", "grok", "ollama", "mix", "self_hosted", "cost_tiered"]
    default_model: str | None = None
    per_agent: dict[str, str] | None = None


class ModeResponse(BaseModel):
    """Server-side view of the current mode + a snapshot of active rules.

    Read-only ``mode`` values are a superset of what ``ApplyModeRequest``
    accepts: "codex" (OPENAI) can come back from `derive_mode()` (a pure-Codex
    global assignment), but there is no `apply_mode="codex"` write path — mix
    mode's per-agent picker is the only way to route to it.
    """

    mode: Literal[
        "anthropic", "grok", "codex", "ollama", "mix", "self_hosted", "cost_tiered"
    ]
    assignments: list[AssignmentResponse]


# =============================================================================
# COMPLEXITY OVERRIDES (cost-tiered routing: compound ROLE(":"complexity) rows)
# =============================================================================


class ComplexityOverrideRequest(BaseModel):
    """Upsert one ROLE(":"complexity) cost-tiered override.

    `role` is validated at the route (not here) against a fixed allowlist —
    a rejected coordinator/board/CEO-facing role gets the deliberate
    tier-pinning message instead of a generic 422. `model_name` must resolve
    to a tier no costlier than that role's `ROLE_MODEL_MAP` baseline
    (downgrade-only by policy), also enforced at the route.
    """

    role: str
    complexity: Literal["low", "high"]
    model_name: str


class ComplexityOverrideResponse(BaseModel):
    """One active ROLE(":"complexity) cost-tiered override row.

    `warning` is set only by the PUT response (never by GET's listing) when
    the model crosses provider families relative to the role's Anthropic
    baseline (e.g. an Anthropic role pinned to a Grok/Ollama/self-hosted
    model) — allowed, but surfaced so it's never a silent switch.
    """

    role: str
    complexity: Literal["low", "high"]
    model_name: str
    warning: str | None = None


# =============================================================================
# ROUTING PRESETS (named, full snapshots of the routing state)
# =============================================================================


class RoutingPresetSummary(BaseModel):
    """One saved preset — list view. No `payload` here; the panel doesn't
    need the snapshot contents until it actually applies one."""

    id: UUID
    name: str
    created_at: datetime


class SaveRoutingPresetRequest(BaseModel):
    """Snapshot the CURRENT routing state under `name`."""

    name: str = Field(..., min_length=1, max_length=100)


class RoutingPresetApplyResponse(BaseModel):
    """Result of applying a preset: the fresh mode snapshot (same shape as
    `ModeResponse`) plus any per-entry skip notes (e.g. a since-removed
    catalog model) — never a partial/silent apply."""

    mode: Literal[
        "anthropic", "grok", "codex", "ollama", "mix", "self_hosted", "cost_tiered"
    ]
    assignments: list[AssignmentResponse]
    skipped: list[str]


def routing_preset_to_summary(row: RoutingPresetTable) -> RoutingPresetSummary:
    """Convert a RoutingPresetTable row to its list-view summary."""
    return RoutingPresetSummary(
        id=require_uuid(row.id), name=row.name, created_at=row.created_at
    )
