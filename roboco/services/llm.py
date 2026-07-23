"""
Model Routing Service

Resolves (provider, model) for a given agent at spawn time using the
scoped rows in `model_assignments`:

    AGENT_SLUG override  >  ROLE(":"complexity) override  >  ROLE override
    >  GLOBAL default

The compound `ROLE(":"complexity)` rung (e.g. "developer:low") is cost-tiered
routing: a task's `estimated_complexity` (LOW/MEDIUM/HIGH, lowercased) lets an
operator pin a role to a cheaper model at a given complexity without touching
the plain ROLE row everything else still uses. It reuses the existing ROLE
scope + `scope_value` column — no schema change — so an absent compound row
is a pure no-op that falls through to the plain ROLE row exactly as before.

If none apply, falls back to the legacy `ROLE_MODEL_MAP` + implicit
Anthropic provider so deployments with zero rows behave exactly as
before. Decryption failures are contained: the service logs the error
and downgrades to the legacy path rather than failing the spawn.

Self-hosted (LOCAL) provider support:
- derive_mode() returns 'self_hosted' when there is exactly one
  GLOBAL assignment pointing to a LOCAL provider.
- apply_mode('self_hosted', ...) enables the LOCAL provider and
  sets a GLOBAL assignment to the given model name.
- upsert_assignment() accepts model names not in the MODEL_CATALOG
  when the target provider is LOCAL (self-hosted models are dynamic;
  they bypass catalog validation).
- resolve_for_agent() checks reachability of the LOCAL base_url
  and falls back to Anthropic if the server is unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

import httpx
import structlog
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from roboco.agents_config import get_agent_role
from roboco.config import settings
from roboco.db.tables import (
    ModelAssignmentTable,
    ProviderConfigTable,
    RoutingPresetTable,
)
from roboco.models.base import AssignmentScope, ModelProvider
from roboco.models.llm_catalog import (
    MODEL_CATALOG_BY_NAME,
    OLLAMA_DEFAULT_MODEL,
)
from roboco.models.runtime import MODEL_MAP, ROLE_MODEL_MAP
from roboco.services.base import BaseService, NotFoundError
from roboco.services.provider import ProviderService, ProviderUpdate
from roboco.utils.converters import require_uuid
from roboco.utils.crypto import EncryptionError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Module-level HTTP helper — decoupled from the service so tests can patch it.
# ---------------------------------------------------------------------------

_OLLAMA_TAGS_TIMEOUT = 5.0  # seconds
_log = structlog.get_logger(__name__)

# Day-1 cost-tiered seed applied by apply_mode('cost_tiered'): (role,
# complexity, model_name). "haiku" is the catalog's cheap Anthropic tier
# (see MODEL_CATALOG_BY_NAME) — developer's LOW-complexity work is
# mechanical/cache-dominated the same way QA already runs on haiku
# (ROLE_MODEL_MAP). developer is the only entry: qa/documenter already
# default to haiku in ROLE_MODEL_MAP (no saving to seed), and cell_pm is
# deliberately excluded from complexity overrides entirely — a coordinator
# role, never offered a row (see _COMPLEXITY_OVERRIDE_ROLES in
# api/routes/provider.py). Extend this tuple to seed more role:complexity
# rows; nothing else needs editing.
_COST_TIERED_SEED: tuple[tuple[str, str, str], ...] = (("developer", "low", "haiku"),)

# derive_mode()'s single-GLOBAL-assignment lookup — a provider type maps to
# its "mode" label 1:1 for every mode `apply_mode` can set via a sole GLOBAL
# row. A dict keeps derive_mode's branch count low (a chain of `if` returns
# hits ruff's PLR0911 the moment a new provider is added, as GEMINI did).
_SINGLE_GLOBAL_MODE_BY_PROVIDER: dict[
    ModelProvider, Literal["grok", "codex", "gemini", "ollama", "self_hosted"]
] = {
    ModelProvider.GROK: "grok",
    ModelProvider.OPENAI: "codex",
    ModelProvider.GEMINI: "gemini",
    ModelProvider.OLLAMA_CLOUD: "ollama",
    ModelProvider.LOCAL: "self_hosted",
}


async def probe_ollama_tags(base_url: str) -> tuple[list[str], str | None]:
    """Fetch the model list from a running Ollama server.

    Hits ``{base_url}/api/tags`` and returns ``(model_names, None)`` on
    success or ``([], error_message)`` on any failure. Never raises.

    Returns:
        A tuple of (list_of_model_name_strings, error_string_or_None).
    """
    url = base_url.rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=_OLLAMA_TAGS_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            models: list[str] = [m["name"] for m in data.get("models", [])]
            return models, None
    except httpx.TimeoutException:
        return [], f"Connection to {base_url} timed out after {_OLLAMA_TAGS_TIMEOUT}s"
    except httpx.ConnectError:
        return [], f"Could not connect to {base_url} — server may be offline"
    except httpx.HTTPStatusError as exc:
        return [], f"Server at {base_url} returned HTTP {exc.response.status_code}"
    except Exception as exc:
        # Log the exception class only — ``str(exc)`` can carry connection
        # internals / stack traces that don't belong in a structured log.
        _log.error(
            "Unexpected error probing Ollama server",
            base_url=base_url,
            error=exc.__class__.__name__,
        )
        return [], "An unexpected error occurred while probing the self-hosted server."


@dataclass(frozen=True)
class AgentRoute:
    """Resolved routing for a single agent spawn.

    `base_url` / `auth_token` being `None` means "Anthropic default":
    orchestrator injects no `ANTHROPIC_*` env vars and the container
    uses its mounted `~/.claude` auth (legacy behaviour).
    """

    provider_id: UUID | None
    provider_type: ModelProvider
    base_url: str | None
    auth_token: str | None
    model_name: str


@dataclass(frozen=True)
class _ResolvedAssignment:
    """Internal — one resolved `model_assignments` row joined to provider."""

    provider: ProviderConfigTable
    model_name: str
    scope: AssignmentScope


# Interactive agents (Intake chat, Secretary chat) have no V1 support on the
# Codex/Gemini providers. A GLOBAL/ROLE assignment pointing them there (e.g.
# the one-click Codex/Gemini mode) is treated as not-applicable at resolution
# time — they fall back to the legacy Anthropic path, so a fleet-wide mode
# switch always yields working chats. An EXPLICIT AGENT_SLUG pin is honored
# here and refused loudly by the orchestrator's spawn guard instead — a
# deliberate operator choice deserves an error, not a silent override. The
# orchestrator imports these as the single source of truth for that guard.
INTERACTIVE_AGENT_SLUGS: tuple[str, ...] = ("intake-1", "secretary-1")
INTERACTIVE_UNSUPPORTED_PROVIDERS: tuple[ModelProvider, ...] = (
    ModelProvider.OPENAI,
    ModelProvider.GEMINI,
)


def _interactive_exempt(agent_slug: str, resolved: _ResolvedAssignment) -> bool:
    """True iff a GLOBAL/ROLE row lands an interactive agent on a
    delivery-only provider — the resolver then keeps it on the legacy path.
    An explicit AGENT_SLUG pin never exempts (kept out of resolve_for_agent
    for its complexity budget)."""
    return (
        agent_slug in INTERACTIVE_AGENT_SLUGS
        and resolved.provider.type in INTERACTIVE_UNSUPPORTED_PROVIDERS
        and resolved.scope is not AssignmentScope.AGENT_SLUG
    )


class ModelRoutingService(BaseService):
    """Resolves per-agent routes from `model_assignments` + legacy fallback."""

    service_name: ClassVar[str] = "model_routing"

    async def resolve_for_agent(
        self, agent_slug: str, complexity: str | None = None
    ) -> AgentRoute:
        """Resolve routing for `agent_slug` using the precedence ladder.

        `complexity` (lowercase "low"/"medium"/"high", from a task's
        `estimated_complexity`) enables the cost-tiered `ROLE(":"complexity)`
        rung — see module docstring. Passing `None` (the default; also what
        every non-task spawn gets) is byte-identical to the pre-cost-tiering
        behavior: with no compound row ever created, this rung is a pure
        no-op regardless of what's passed here.

        Never raises for a normal agent — decrypt failures, unreachable
        self-hosted servers, and missing agents all downgrade to the
        legacy Anthropic path, because a stalled spawn is worse than a
        routing miss.
        """
        role = get_agent_role(agent_slug) or ""
        resolved = await self._resolve_assignment(agent_slug, role, complexity)
        if resolved is not None and _interactive_exempt(agent_slug, resolved):
            # A fleet-wide GLOBAL/ROLE row landed an interactive agent on a
            # delivery-only provider (e.g. the one-click Codex/Gemini mode).
            # Not applicable to Intake/Secretary — keep their chats working
            # on the legacy Anthropic path. An explicit AGENT_SLUG pin is
            # NOT exempted; the orchestrator's spawn guard refuses it loudly.
            self.log.info(
                "Interactive agent exempt from delivery-only provider",
                agent_slug=agent_slug,
                provider_type=resolved.provider.type.value,
                scope=resolved.scope.value,
            )
            return self._legacy_route(role)
        if resolved is not None and resolved.provider.enabled:
            route = await self._route_from_resolved(resolved, agent_slug)
            if route is not None:
                return route
        elif resolved is not None and not resolved.provider.enabled:
            # Configured but disabled — distinguishable from "no assignment"
            # so the bypass is surfaced, not silent. Default stays graceful (a
            # stalled spawn is worse than a routing miss); ROBOCO_ROUTING_STRICT
            # opts into fail-closed for operators who'd rather it stall.
            self.log.warning(
                "Configured provider is disabled; downgrading to legacy Anthropic path",
                agent_slug=agent_slug,
                role=role,
                provider_id=str(resolved.provider.id),
            )
            if settings.routing_strict:
                raise RuntimeError(
                    f"routing_strict: agent {agent_slug!r} has a disabled configured "
                    f"provider {resolved.provider.id}; refusing to silently downgrade "
                    f"to the legacy Anthropic path"
                )
        return self._legacy_route(role)

    async def _resolve_assignment(
        self, agent_slug: str, role: str, complexity: str | None = None
    ) -> _ResolvedAssignment | None:
        """Walk the precedence ladder:

        agent override > role+complexity override > role override > global.

        The role+complexity rung tries the compound `scope_value`
        (e.g. "developer:low") under the existing ROLE scope before falling
        to the plain role row — reusing AssignmentScope.ROLE, no schema
        change. A missing compound row (the common case — cost-tiering is
        opt-in) falls straight through to the plain ROLE lookup below.
        """
        resolved = await self._find_assignment(
            scope=AssignmentScope.AGENT_SLUG, scope_value=agent_slug
        )
        if resolved is None and role and complexity:
            resolved = await self._find_assignment(
                scope=AssignmentScope.ROLE, scope_value=f"{role}:{complexity}"
            )
        if resolved is None and role:
            resolved = await self._find_assignment(
                scope=AssignmentScope.ROLE, scope_value=role
            )
        if resolved is None:
            resolved = await self._find_assignment(
                scope=AssignmentScope.GLOBAL, scope_value=None
            )
        return resolved

    async def _route_from_resolved(
        self, resolved: _ResolvedAssignment, agent_slug: str
    ) -> AgentRoute | None:
        """Build a route from a resolved+enabled assignment.

        Returns ``None`` to signal the caller should fall through to the
        legacy Anthropic path (unreachable self-hosted server, empty
        base_url, or a token-decrypt failure).
        """
        if resolved.provider.type == ModelProvider.LOCAL:
            return await self._local_route_or_none(resolved, agent_slug)
        return await self._decrypt_route_or_none(resolved, agent_slug)

    async def _local_route_or_none(
        self, resolved: _ResolvedAssignment, agent_slug: str
    ) -> AgentRoute | None:
        """Route to a LOCAL provider only if it is configured and reachable.

        Probes ``{base_url}/api/tags`` first; if the server is down (or no
        base_url is configured) returns ``None`` so the spawn falls back to
        Anthropic — better a wrong provider than no spawn.
        """
        base_url = resolved.provider.base_url or ""
        if not base_url:
            return None  # unconfigured → fall through
        _, error = await probe_ollama_tags(base_url)
        if error is not None:
            self.log.warning(
                "Self-hosted server unreachable; falling back to Anthropic",
                base_url=base_url,
                error=error,
                agent_slug=agent_slug,
            )
            return None
        return await self._decrypt_route_or_none(resolved, agent_slug)

    async def _decrypt_route_or_none(
        self, resolved: _ResolvedAssignment, agent_slug: str
    ) -> AgentRoute | None:
        """Build the route, downgrading to ``None`` on a token-decrypt failure."""
        try:
            return await self._route_from_assignment(resolved)
        except EncryptionError:
            self.log.error(
                "Provider token decrypt failed; falling back to legacy path",
                provider_id=str(resolved.provider.id),
                agent_slug=agent_slug,
            )
            return None

    def _legacy_route(self, role: str) -> AgentRoute:
        """Legacy fallback: role-default short name through MODEL_MAP."""
        short = ROLE_MODEL_MAP.get(role, "sonnet")
        return AgentRoute(
            provider_id=None,
            provider_type=ModelProvider.ANTHROPIC,
            base_url=None,
            auth_token=None,
            model_name=MODEL_MAP.get(short, short),
        )

    # =========================================================================
    # ASSIGNMENT CRUD (consumed by api/routes/provider.py)
    # =========================================================================

    async def list_assignments(self) -> list[ModelAssignmentTable]:
        result = await self.session.execute(
            select(ModelAssignmentTable).order_by(
                ModelAssignmentTable.scope, ModelAssignmentTable.scope_value
            )
        )
        return list(result.scalars().all())

    async def get_assignment(
        self, *, scope: AssignmentScope, scope_value: str | None
    ) -> ModelAssignmentTable | None:
        query = select(ModelAssignmentTable).where(ModelAssignmentTable.scope == scope)
        if scope_value is None:
            query = query.where(ModelAssignmentTable.scope_value.is_(None))
        else:
            query = query.where(ModelAssignmentTable.scope_value == scope_value)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def upsert_assignment(
        self,
        *,
        scope: AssignmentScope,
        scope_value: str | None,
        model_name: str,
        provider_type_override: ModelProvider | None = None,
    ) -> ModelAssignmentTable:
        """Insert-or-update (by unique (scope, scope_value)).

        Provider is normally derived from `MODEL_CATALOG` — the UI never
        picks a provider separately, so the service looks up the pre-seeded
        provider row for the catalog entry's type.

        When `provider_type_override` is supplied (used internally by
        `apply_mode('self_hosted', ...)` and mix mode for LOCAL models),
        the catalog look-up is skipped and the named provider type is used
        directly. This allows self-hosted model names (which are not in the
        static catalog) to be assigned to the LOCAL provider.
        """
        self._validate_scope(scope, scope_value)

        if provider_type_override is not None:
            provider = await self._get_seeded_provider(provider_type_override)
            provider_type_for_log = provider_type_override
        else:
            entry = MODEL_CATALOG_BY_NAME.get(model_name)
            if entry is None:
                # Try to route to LOCAL if a LOCAL provider is seeded — this
                # allows self-hosted model names in mix mode without an error.
                local_provider = await self._find_local_provider()
                if local_provider is None:
                    raise ValueError(
                        f"Unknown model '{model_name}'. Use one from "
                        "GET /api/providers/catalog."
                    )
                provider = local_provider
                provider_type_for_log = ModelProvider.LOCAL
            else:
                provider = await self._get_seeded_provider(entry.provider_type)
                provider_type_for_log = entry.provider_type

        # Whenever an assignment resolves to LOCAL/GEMINI/OPENAI, ensure the
        # provider row is enabled so resolve_for_agent() will actually use it
        # instead of silently falling back to Anthropic. GROK is deliberately
        # excluded — its enable state is gated on the xAI key
        # (set_grok_api_key), unlike LOCAL/Codex/Gemini which have no key to
        # gate on (self-hosted's own base_url + mounted-subscription auth).
        if provider_type_for_log in (
            ModelProvider.LOCAL,
            ModelProvider.GEMINI,
            ModelProvider.OPENAI,
        ):
            provider_svc = ProviderService(self.session)
            await provider_svc.update_provider(
                require_uuid(provider.id), ProviderUpdate(enabled=True)
            )

        row = await self.get_assignment(scope=scope, scope_value=scope_value)
        if row is None:
            row = ModelAssignmentTable(
                scope=scope,
                scope_value=scope_value,
                provider_config_id=provider.id,
                model_name=model_name,
            )
            self.session.add(row)
        else:
            row.provider_config_id = cast("Any", provider.id)
            row.model_name = model_name

        await self.session.flush()
        self.log.info(
            "Assignment upserted",
            scope=scope.value,
            scope_value=scope_value,
            provider_type=provider_type_for_log.value,
            model_name=model_name,
        )
        return row

    async def derive_mode(
        self,
    ) -> Literal[
        "anthropic", "grok", "codex", "gemini", "ollama", "mix", "self_hosted"
    ]:
        """Return the current "mode" label for the Settings UI.

        Decision tree matches what `apply_mode` writes:
          - no assignments at all           → "anthropic"
          - only a global row, Ollama Cloud → "ollama"
          - only a global row, LOCAL        → "self_hosted"
          - only a global row, GROK         → "grok"
          - only a global row, OPENAI       → "codex"
          - only a global row, GEMINI       → "gemini"
          - anything else                   → "mix"
        """
        assignments = await self.list_assignments()
        if not assignments:
            return "anthropic"
        only_global = (
            len(assignments) == 1 and assignments[0].scope == AssignmentScope.GLOBAL
        )
        if only_global:
            mode = _SINGLE_GLOBAL_MODE_BY_PROVIDER.get(assignments[0].provider.type)
            if mode is not None:
                return mode
        return "mix"

    async def set_ollama_api_key(self, api_key: str) -> ProviderConfigTable:
        """Set / clear the Ollama Cloud provider's API key.

        Empty string clears + disables; a real key encrypts + enables.
        Operates on the single pre-seeded Ollama row — no provider
        creation happens here.
        """
        provider = await self._get_seeded_provider(ModelProvider.OLLAMA_CLOUD)
        provider_svc = ProviderService(self.session)
        await provider_svc.update_provider(
            require_uuid(provider.id),
            ProviderUpdate(
                auth_token=api_key if api_key else None,
                clear_auth_token=not api_key,
                enabled=bool(api_key),
            ),
        )
        # Re-fetch for the caller.
        return await self._get_seeded_provider(ModelProvider.OLLAMA_CLOUD)

    async def set_grok_api_key(self, api_key: str) -> ProviderConfigTable:
        """Set / clear the Grok (xAI) provider's API key.

        Empty string clears + disables; a real key encrypts + enables.
        Operates on the single pre-seeded Grok row — no provider creation
        happens here. The key is the standard xAI key used against
        https://api.x.ai/v1.
        """
        provider = await self._get_seeded_provider(ModelProvider.GROK)
        provider_svc = ProviderService(self.session)
        await provider_svc.update_provider(
            require_uuid(provider.id),
            ProviderUpdate(
                auth_token=api_key if api_key else None,
                clear_auth_token=not api_key,
                enabled=bool(api_key),
            ),
        )
        # Re-fetch for the caller.
        return await self._get_seeded_provider(ModelProvider.GROK)

    async def resolve_provider_for_model(
        self, model_name: str
    ) -> ProviderConfigTable | None:
        """Resolve which provider row `model_name` would route to — catalog
        lookup first, LOCAL fallback for self-hosted names (mirrors
        `upsert_assignment`'s own resolution order). ``None`` means the model
        is genuinely unknown: no catalog entry AND no LOCAL provider seeded.

        Read-only — exposed so a caller (the complexity-override endpoint)
        can check `.enabled` / `.base_url` on the resolved row BEFORE writing
        an assignment, instead of writing first and discovering at spawn time
        that the target provider was never configured.
        """
        entry = MODEL_CATALOG_BY_NAME.get(model_name)
        if entry is not None:
            return await self._get_seeded_provider(entry.provider_type)
        return await self._find_local_provider()

    async def _get_seeded_provider(
        self, provider_type: ModelProvider
    ) -> ProviderConfigTable:
        """Find the single seeded provider row for `provider_type`.

        Migration `004_provider_routing` seeds exactly one row per type —
        we just look it up. Raises NotFoundError if the seed is missing
        (e.g., migration hasn't been applied).
        """
        result = await self.session.execute(
            select(ProviderConfigTable).where(ProviderConfigTable.type == provider_type)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                resource_type="Provider",
                resource_id=f"type={provider_type.value}",
            )
        return row

    async def delete_assignment(
        self, *, scope: AssignmentScope, scope_value: str | None
    ) -> None:
        row = await self.get_assignment(scope=scope, scope_value=scope_value)
        if row is None:
            raise NotFoundError(
                resource_type="ModelAssignment",
                resource_id=f"{scope.value}:{scope_value or '-'}",
            )
        await self.session.delete(row)
        await self.session.flush()
        self.log.info(
            "Assignment deleted",
            scope=scope.value,
            scope_value=scope_value,
        )

    async def apply_mode(
        self,
        *,
        mode: str,
        default_model: str | None = None,
        per_agent: dict[str, str] | None = None,
    ) -> None:
        """Apply a routing "mode" in a single transactional call.

        All modes below preserve AGENT_SLUG pins AND compound ROLE(":"complexity)
        cost-tier overrides — only plain ROLE/GLOBAL rows are replaced, so a
        per-agent override or a curated complexity override both survive a
        mode switch (mixed-provider routing is already a supported state; see
        "mix"). See `_wipe_mode_switch_assignments` for why both are spared.

        Modes:
          - "anthropic":   wipe role/global assignments so every spawn falls
            through to the legacy ROLE_MODEL_MAP + mounted ~/.claude path.
          - "ollama":      wipe role/global assignments, set GLOBAL to the given
            Ollama model (default: OLLAMA_DEFAULT_MODEL).
          - "self_hosted": wipe role/global assignments, enable the LOCAL
            provider, and set the GLOBAL default to `default_model` (a
            self-hosted model name — not validated against the static catalog).
          - "grok":        wipe role/global assignments, set the GLOBAL default
            to a Grok (xAI) model (default grok-build-0.1). Requires the xAI key.
          - "codex":       wipe role/global assignments, force-enable the OPENAI
            provider, set the GLOBAL default to a Codex model (default
            gpt-5.3-codex). No key check — subscription-CLI auth (~/.codex),
            same shape as Grok/self_hosted.
          - "gemini":      wipe role/global assignments, force-enable the GEMINI
            provider, set the GLOBAL default to a Gemini model (default
            gemini-2.5-pro). No key check — subscription-CLI auth (~/.gemini),
            same shape as Grok/self_hosted.
          - "mix":         apply per-agent map verbatim. Any agent not in the
            map falls through to the GLOBAL default — which is whatever it
            was (preserves prior state). Self-hosted model names (not in the
            catalog) are automatically routed to the LOCAL provider. An empty
            map (not None) is the explicit clear-all — every pin deleted,
            nothing re-added (see `_apply_mix`).
          - "cost_tiered": UNLIKE every mode above, this does NOT wipe
            anything — it seeds/re-upserts the day-1 `_COST_TIERED_SEED`
            compound ROLE(":"complexity) rows on top of whatever routing is
            already in place (AGENT_SLUG pins, ROLE rows, GLOBAL default all
            untouched). Idempotent: re-applying just re-upserts the same rows.
            Only ever reached via an explicit PUT/POST call (this method has
            exactly one caller: the `POST /providers` route) — never from
            startup, a migration, or a background loop.
        """
        if mode == "anthropic":
            await self._apply_anthropic()
        elif mode == "grok":
            await self._apply_grok(default_model)
        elif mode == "codex":
            await self._apply_codex(default_model)
        elif mode == "gemini":
            await self._apply_gemini(default_model)
        elif mode == "ollama":
            await self._apply_ollama(default_model)
        elif mode == "self_hosted":
            await self._apply_self_hosted(default_model)
        elif mode == "mix":
            await self._apply_mix(per_agent)
        elif mode == "cost_tiered":
            await self._apply_cost_tiered()
        else:
            raise ValueError(
                f"Unknown mode '{mode}'."
                " Use 'anthropic', 'grok', 'codex', 'gemini', 'ollama',"
                " 'self_hosted', 'mix', or 'cost_tiered'."
            )

    async def _wipe_mode_switch_assignments(self) -> None:
        """Delete plain ROLE/GLOBAL assignments on a mode switch — sparing two
        curated layers that behave like per-agent pins, not "mode" state:

        - AGENT_SLUG pins (the original carve-out).
        - Compound ROLE(":"complexity) cost-tier overrides — an operator-built
          curated layer exactly like AGENT_SLUG pins, same rationale: they're
          deliberate, individually-authored routing decisions, not part of the
          coarse "flip everyone to X" a mode switch represents.

        Without the second carve-out, flipping to Anthropic/Grok/Ollama/
        Self-Hosted silently wiped complexity overrides behind a success
        toast — a repeat of the 2026-07-17 incident where these same buttons
        wiped AGENT_SLUG pins. `_apply_mix` doesn't call this (it never
        touches ROLE/GLOBAL rows at all, so compound rows were already safe
        there); `_apply_cost_tiered` doesn't either (it's purely additive).
        """
        await self.session.execute(
            sa_delete(ModelAssignmentTable).where(
                ModelAssignmentTable.scope != AssignmentScope.AGENT_SLUG,
                ~(
                    (ModelAssignmentTable.scope == AssignmentScope.ROLE)
                    & ModelAssignmentTable.scope_value.contains(":")
                ),
            )
        )
        await self.session.flush()

    async def _apply_anthropic(self) -> None:
        """Wipe role/global assignments so every spawn uses the legacy Anthropic
        path. AGENT_SLUG pins and complexity overrides are preserved — see
        `_wipe_mode_switch_assignments`."""
        await self._wipe_mode_switch_assignments()
        self.log.info("Mode applied: anthropic (role/global assignments cleared)")

    async def _apply_grok(self, default_model: str | None) -> None:
        """Wipe assignments, set the GLOBAL default to a Grok (xAI) model.

        The GrokCliProvider authenticates via the SuperGrok subscription
        (``~/.grok/auth.json``), not the xAI API key — so the GROK provider row
        must be enabled here for resolve_for_agent() to route to it, mirroring
        self_hosted enabling LOCAL. Without it the seeded GROK row stays
        disabled (no key set) and agents fall back to Anthropic at spawn even
        in grok mode. AGENT_SLUG pins and complexity overrides are preserved
        (see `_wipe_mode_switch_assignments`).
        """
        await self._wipe_mode_switch_assignments()
        grok = await self._get_seeded_provider(ModelProvider.GROK)
        provider_svc = ProviderService(self.session)
        await provider_svc.update_provider(
            require_uuid(grok.id),
            ProviderUpdate(enabled=True),
        )
        model_name = default_model or "grok-build-0.1"
        await self.upsert_assignment(
            scope=AssignmentScope.GLOBAL,
            scope_value=None,
            model_name=model_name,
        )
        self.log.info("Mode applied: grok", default_model=model_name)

    async def _apply_codex(self, default_model: str | None) -> None:
        """Wipe assignments, set the GLOBAL default to a Codex (OpenAI) model.

        Migration 083 already seeds the OPENAI provider row `enabled=true`
        (there's no key to withhold behind a disabled row — subscription
        auth via a mounted `~/.codex`), but this mode's own force-enable is
        belt-and-suspenders against a row disabled by some other path,
        mirroring `_apply_grok`. AGENT_SLUG pins and complexity overrides are
        preserved (see `_wipe_mode_switch_assignments`).
        """
        await self._wipe_mode_switch_assignments()
        codex = await self._get_seeded_provider(ModelProvider.OPENAI)
        provider_svc = ProviderService(self.session)
        await provider_svc.update_provider(
            require_uuid(codex.id),
            ProviderUpdate(enabled=True),
        )
        model_name = default_model or "gpt-5.3-codex"
        await self.upsert_assignment(
            scope=AssignmentScope.GLOBAL,
            scope_value=None,
            model_name=model_name,
        )
        self.log.info("Mode applied: codex", default_model=model_name)

    async def _apply_gemini(self, default_model: str | None) -> None:
        """Wipe assignments, set the GLOBAL default to a Gemini (Google) model.

        Migration 085 seeded the GEMINI provider row `enabled=false`
        (migration 086 flips it to `enabled=true` at rest, matching Codex),
        so this mode's force-enable is the same belt-and-suspenders step
        `_apply_grok` runs for GROK — the mode switch must not depend on the
        seed migration alone. GeminiCliProvider authenticates via a mounted
        OAuth credential (`~/.gemini`), not a stored API key, so there is no
        key-check precondition. AGENT_SLUG pins and complexity overrides are
        preserved (see `_wipe_mode_switch_assignments`).
        """
        await self._wipe_mode_switch_assignments()
        gemini = await self._get_seeded_provider(ModelProvider.GEMINI)
        provider_svc = ProviderService(self.session)
        await provider_svc.update_provider(
            require_uuid(gemini.id),
            ProviderUpdate(enabled=True),
        )
        model_name = default_model or "gemini-2.5-pro"
        await self.upsert_assignment(
            scope=AssignmentScope.GLOBAL,
            scope_value=None,
            model_name=model_name,
        )
        self.log.info("Mode applied: gemini", default_model=model_name)

    async def _apply_ollama(self, default_model: str | None) -> None:
        """Wipe role/global assignments, set GLOBAL to an Ollama Cloud model.

        AGENT_SLUG pins and complexity overrides are preserved (see
        `_wipe_mode_switch_assignments`)."""
        await self._wipe_mode_switch_assignments()
        model_name = default_model or OLLAMA_DEFAULT_MODEL
        await self.upsert_assignment(
            scope=AssignmentScope.GLOBAL,
            scope_value=None,
            model_name=model_name,
        )
        self.log.info("Mode applied: ollama", default_model=model_name)

    async def _apply_self_hosted(self, default_model: str | None) -> None:
        """Wipe role/global assignments, enable the LOCAL provider, point GLOBAL
        at it. AGENT_SLUG pins and complexity overrides are preserved (see
        `_wipe_mode_switch_assignments`)."""
        if not default_model:
            raise ValueError(
                "self_hosted mode requires a default_model (self-hosted model name)"
            )
        await self._wipe_mode_switch_assignments()
        # Enable the LOCAL provider row so resolve_for_agent() will use it.
        local = await self._find_local_provider()
        if local is None:
            raise NotFoundError(
                resource_type="Provider",
                resource_id=f"type={ModelProvider.LOCAL.value}",
            )
        provider_svc = ProviderService(self.session)
        await provider_svc.update_provider(
            require_uuid(local.id),
            ProviderUpdate(enabled=True),
        )
        await self.upsert_assignment(
            scope=AssignmentScope.GLOBAL,
            scope_value=None,
            model_name=default_model,
            provider_type_override=ModelProvider.LOCAL,
        )
        self.log.info("Mode applied: self_hosted", default_model=default_model)

    async def _apply_mix(self, per_agent: dict[str, str] | None) -> None:
        """Apply a per-agent override map; leave role + global rows untouched.

        An EMPTY map is the explicit clear-all: every AGENT_SLUG pin is
        deleted and nothing is re-added, so all agents fall back to the
        role/global layer. Without this there was no way out of mix mode at
        all — mode switches deliberately spare pins, and a fully-pinned fleet
        (the live 2026-07-23 incident: 25/25 agents pinned) made every mode
        button an effective no-op forever. ``None`` (map not provided) is
        still refused — only a deliberate empty map clears.
        """
        if per_agent is None:
            raise ValueError("mix mode requires a per_agent map")
        # Clear existing agent-slug overrides so the new map is authoritative.
        await self.session.execute(
            sa_delete(ModelAssignmentTable).where(
                ModelAssignmentTable.scope == AssignmentScope.AGENT_SLUG
            )
        )
        await self.session.flush()
        for agent_slug, model_name in per_agent.items():
            if not model_name:
                continue
            # upsert_assignment will route to LOCAL for non-catalog names.
            await self.upsert_assignment(
                scope=AssignmentScope.AGENT_SLUG,
                scope_value=agent_slug,
                model_name=model_name,
            )
        self.log.info("Mode applied: mix", agents=len(per_agent))

    async def _apply_cost_tiered(self) -> None:
        """Seed the day-1 cost-tiered compound overrides (see `_COST_TIERED_SEED`).

        Unlike every other mode, this does not delete anything first — it is
        a pure additive upsert on top of whatever routing already exists, so
        AGENT_SLUG pins, plain ROLE rows, and the GLOBAL default all survive
        untouched. Idempotent: re-running just re-upserts the same two rows.
        """
        for role, complexity, model_name in _COST_TIERED_SEED:
            await self.upsert_assignment(
                scope=AssignmentScope.ROLE,
                scope_value=f"{role}:{complexity}",
                model_name=model_name,
            )
        self.log.info(
            "Mode applied: cost_tiered",
            seeded=[f"{r}:{c}->{m}" for r, c, m in _COST_TIERED_SEED],
        )

    # =========================================================================
    # ROUTING PRESETS (named, full snapshots — consumed by api/routes/provider.py)
    # =========================================================================

    async def list_routing_presets(self) -> list[RoutingPresetTable]:
        """List saved presets, newest first (payload included; the route
        strips it down to id/name/created_at for the list response)."""
        result = await self.session.execute(
            select(RoutingPresetTable).order_by(RoutingPresetTable.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_routing_preset(self, preset_id: UUID) -> RoutingPresetTable | None:
        return await self.session.get(RoutingPresetTable, preset_id)

    async def save_routing_preset(self, name: str) -> RoutingPresetTable:
        """Snapshot the FULL current routing state under `name`.

        Captures exactly what `GET /providers` + `GET /providers/complexity-
        overrides` already serve — the derived mode label plus every current
        `model_assignments` row (GLOBAL / plain ROLE / compound
        ROLE(":"complexity) / AGENT_SLUG all together) — so a preset is "what
        the card currently shows". Raises ValueError on an empty or
        already-taken name (the route maps that to 409).
        """
        if not name:
            raise ValueError("Preset name must not be empty")
        existing = await self.session.execute(
            select(RoutingPresetTable).where(RoutingPresetTable.name == name)
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"A preset named '{name}' already exists")

        mode = await self.derive_mode()
        assignments = await self.list_assignments()
        payload: dict[str, Any] = {
            "mode": mode,
            "assignments": [
                {
                    "scope": a.scope.value,
                    "scope_value": a.scope_value,
                    "provider_type": a.provider.type.value,
                    "model_name": a.model_name,
                }
                for a in assignments
            ],
        }
        row = RoutingPresetTable(name=name, payload=payload)
        self.session.add(row)
        await self.session.flush()
        self.log.info("Routing preset saved", name=name, rows=len(assignments))
        return row

    async def delete_routing_preset(self, preset_id: UUID) -> None:
        row = await self.get_routing_preset(preset_id)
        if row is None:
            raise NotFoundError(
                resource_type="RoutingPreset", resource_id=str(preset_id)
            )
        await self.session.delete(row)
        await self.session.flush()
        self.log.info("Routing preset deleted", name=row.name)

    async def _validate_preset_entry(
        self, entry: dict[str, Any]
    ) -> tuple[AssignmentScope, str | None, str] | None:
        """Validate one preset payload entry WITHOUT writing anything.

        Replicates every check `upsert_assignment` would apply — scope shape
        (`_validate_scope`), a resolvable provider (`resolve_provider_for_model`),
        AND that provider's current `.enabled` state — so `apply_routing_preset`
        can vet the whole payload before touching the DB. The `.enabled` check
        catches a preset saved while a provider was live (a key set, self-hosted
        connected, Codex/Gemini enabled) that has since gone disabled: applying
        it would otherwise silently restore a dead assignment that resolves
        through to the legacy Anthropic fallback at spawn — the same class of
        bug `resolve_for_agent`'s own disabled-provider branch guards against.
        Returns the parsed `(scope, scope_value, model_name)` tuple when valid,
        else `None`.
        """
        model_name = entry.get("model_name")
        scope_raw = entry.get("scope")
        if not model_name or not isinstance(scope_raw, str):
            return None
        try:
            scope = AssignmentScope(scope_raw)
            scope_value = entry.get("scope_value")
            self._validate_scope(scope, scope_value)
        except ValueError:
            return None
        try:
            provider = await self.resolve_provider_for_model(model_name)
        except NotFoundError:
            return None
        if provider is None or not provider.enabled:
            return None
        return scope, scope_value, model_name

    async def apply_routing_preset(self, preset_id: UUID) -> list[str]:
        """Replace EVERY current `model_assignments` row with the preset's
        snapshot — a full swap, unlike `apply_mode()`'s pin-preserving modes:
        a preset's whole point is restoring the exact full state it captured,
        AGENT_SLUG pins included.

        Validate-all-FIRST: every payload entry is checked (scope shape +
        a resolvable provider — the same rules `upsert_assignment` enforces)
        BEFORE anything is deleted, so the wipe never runs on the strength of
        a payload that hasn't been fully vetted. Only entries that validated
        are written; a since-removed catalog model (or any other now-invalid
        entry) is skipped and reported in the returned notes — it never
        aborts the entries that DID validate. Nothing here calls
        `session.commit()` (the route does, once, after this returns), so an
        unexpected failure during the write phase leaves the prior routing
        state intact once the caller's transaction rolls back rather than
        landing half-swapped.
        """
        preset = await self.get_routing_preset(preset_id)
        if preset is None:
            raise NotFoundError(
                resource_type="RoutingPreset", resource_id=str(preset_id)
            )

        valid: list[tuple[AssignmentScope, str | None, str]] = []
        notes: list[str] = []
        for entry in preset.payload.get("assignments", []):
            parsed = await self._validate_preset_entry(entry)
            if parsed is None:
                notes.append(
                    f"Skipped {entry.get('scope')}:{entry.get('scope_value')} "
                    f"({entry.get('model_name')}) — invalid or unavailable "
                    "model/scope"
                )
            else:
                valid.append(parsed)

        # Only now — every remaining entry has been vetted — replace the
        # current routing state.
        await self.session.execute(sa_delete(ModelAssignmentTable))
        await self.session.flush()
        for scope, scope_value, model_name in valid:
            await self.upsert_assignment(
                scope=scope, scope_value=scope_value, model_name=model_name
            )

        self.log.info("Routing preset applied", name=preset.name, skipped=len(notes))
        return notes

    # =========================================================================
    # INTERNAL
    # =========================================================================

    async def _find_assignment(
        self, *, scope: AssignmentScope, scope_value: str | None
    ) -> _ResolvedAssignment | None:
        row = await self.get_assignment(scope=scope, scope_value=scope_value)
        if row is None:
            return None
        # Relationship is lazy="joined" in the ORM so `.provider` is loaded.
        return _ResolvedAssignment(
            provider=row.provider, model_name=row.model_name, scope=row.scope
        )

    async def _find_local_provider(self) -> ProviderConfigTable | None:
        """Return the LOCAL provider row, or None if not seeded."""
        result = await self.session.execute(
            select(ProviderConfigTable).where(
                ProviderConfigTable.type == ModelProvider.LOCAL
            )
        )
        return result.scalar_one_or_none()

    async def _route_from_assignment(self, resolved: _ResolvedAssignment) -> AgentRoute:
        provider = resolved.provider
        # Decrypt only when the provider has a stored token (ollama_cloud).
        # Anthropic providers have `auth_token_encrypted=NULL` and use the
        # container's mounted credentials — no env injection needed.
        provider_uuid = require_uuid(provider.id)
        auth_token: str | None = None
        if provider.auth_token_encrypted:
            provider_svc = ProviderService(self.session)
            auth_token = await provider_svc.get_decrypted_token(provider_uuid)

        return AgentRoute(
            provider_id=provider_uuid,
            provider_type=provider.type,
            base_url=provider.base_url,
            auth_token=auth_token,
            model_name=resolved.model_name,
        )

    @staticmethod
    def _validate_scope(scope: AssignmentScope, scope_value: str | None) -> None:
        if scope == AssignmentScope.GLOBAL and scope_value is not None:
            raise ValueError("global scope must have scope_value=None")
        if (
            scope in (AssignmentScope.ROLE, AssignmentScope.AGENT_SLUG)
            and not scope_value
        ):
            raise ValueError(f"{scope.value} scope requires a non-empty scope_value")


def get_model_routing_service(session: AsyncSession) -> ModelRoutingService:
    """Get a ModelRoutingService instance."""
    return ModelRoutingService(session)
