"""
Model Routing Service

Resolves (provider, model) for a given agent at spawn time using the
scoped rows in `model_assignments`:

    AGENT_SLUG override  >  ROLE override  >  GLOBAL default

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
from roboco.db.tables import ModelAssignmentTable, ProviderConfigTable
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
        _log.error(
            "Unexpected error probing Ollama server",
            base_url=base_url,
            error=str(exc),
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


class ModelRoutingService(BaseService):
    """Resolves per-agent routes from `model_assignments` + legacy fallback."""

    service_name: ClassVar[str] = "model_routing"

    async def resolve_for_agent(self, agent_slug: str) -> AgentRoute:
        """Resolve routing for `agent_slug` using the precedence ladder.

        Never raises for a normal agent — decrypt failures, unreachable
        self-hosted servers, and missing agents all downgrade to the
        legacy Anthropic path, because a stalled spawn is worse than a
        routing miss.
        """
        role = get_agent_role(agent_slug) or ""
        resolved = await self._resolve_assignment(agent_slug, role)
        if resolved is not None and resolved.provider.enabled:
            route = await self._route_from_resolved(resolved, agent_slug)
            if route is not None:
                return route
        return self._legacy_route(role)

    async def _resolve_assignment(
        self, agent_slug: str, role: str
    ) -> _ResolvedAssignment | None:
        """Walk the precedence ladder: agent override > role override > global."""
        resolved = await self._find_assignment(
            scope=AssignmentScope.AGENT_SLUG, scope_value=agent_slug
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

        # Whenever an assignment resolves to LOCAL, ensure the LOCAL provider
        # row is enabled so resolve_for_agent() will actually use it.
        if provider_type_for_log == ModelProvider.LOCAL:
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

    async def derive_mode(self) -> Literal["anthropic", "ollama", "mix", "self_hosted"]:
        """Return the current "mode" label for the Settings UI.

        Decision tree matches what `apply_mode` writes:
          - no assignments at all           → "anthropic"
          - only a global row, Ollama Cloud → "ollama"
          - only a global row, LOCAL        → "self_hosted"
          - anything else                   → "mix"
        """
        assignments = await self.list_assignments()
        if not assignments:
            return "anthropic"
        only_global = (
            len(assignments) == 1 and assignments[0].scope == AssignmentScope.GLOBAL
        )
        if only_global:
            if assignments[0].provider.type == ModelProvider.OLLAMA_CLOUD:
                return "ollama"
            if assignments[0].provider.type == ModelProvider.LOCAL:
                return "self_hosted"
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

        Modes:
          - "anthropic":   wipe all assignments so every spawn falls through
            to the legacy ROLE_MODEL_MAP + mounted ~/.claude path.
          - "ollama":      wipe role/agent overrides, set GLOBAL to the given
            Ollama model (default: OLLAMA_DEFAULT_MODEL). CEO-type pins can be
            layered back manually if the user wants them.
          - "self_hosted": wipe all assignments, enable the LOCAL provider,
            and set the GLOBAL default to `default_model` (a self-hosted
            model name — not validated against the static catalog).
          - "mix":         apply per-agent map verbatim. Any agent not in the
            map falls through to the GLOBAL default — which is whatever it
            was (preserves prior state). Self-hosted model names (not in the
            catalog) are automatically routed to the LOCAL provider.
        """
        if mode == "anthropic":
            await self._apply_anthropic()
        elif mode == "ollama":
            await self._apply_ollama(default_model)
        elif mode == "self_hosted":
            await self._apply_self_hosted(default_model)
        elif mode == "mix":
            await self._apply_mix(per_agent)
        else:
            raise ValueError(
                f"Unknown mode '{mode}'."
                " Use 'anthropic', 'ollama', 'self_hosted', or 'mix'."
            )

    async def _apply_anthropic(self) -> None:
        """Wipe all assignments so every spawn uses the legacy Anthropic path."""
        await self.session.execute(sa_delete(ModelAssignmentTable))
        await self.session.flush()
        self.log.info("Mode applied: anthropic (all assignments cleared)")

    async def _apply_ollama(self, default_model: str | None) -> None:
        """Wipe assignments, set the GLOBAL default to an Ollama Cloud model."""
        await self.session.execute(sa_delete(ModelAssignmentTable))
        await self.session.flush()
        model_name = default_model or OLLAMA_DEFAULT_MODEL
        await self.upsert_assignment(
            scope=AssignmentScope.GLOBAL,
            scope_value=None,
            model_name=model_name,
        )
        self.log.info("Mode applied: ollama", default_model=model_name)

    async def _apply_self_hosted(self, default_model: str | None) -> None:
        """Wipe assignments, enable the LOCAL provider, point GLOBAL at it."""
        if not default_model:
            raise ValueError(
                "self_hosted mode requires a default_model (self-hosted model name)"
            )
        await self.session.execute(sa_delete(ModelAssignmentTable))
        await self.session.flush()
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
        """Apply a per-agent override map; leave role + global rows untouched."""
        if not per_agent:
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
        return _ResolvedAssignment(provider=row.provider, model_name=row.model_name)

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
