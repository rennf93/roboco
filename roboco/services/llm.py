"""
Model Routing Service

Resolves (provider, model) for a given agent at spawn time using the
scoped rows in `model_assignments`:

    AGENT_SLUG override  >  ROLE override  >  GLOBAL default

If none apply, falls back to the legacy `ROLE_MODEL_MAP` + implicit
Anthropic provider so deployments with zero rows behave exactly as
before. Decryption failures are contained: the service logs the error
and downgrades to the legacy path rather than failing the spawn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

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

        Never raises for a normal agent — decrypt failures and missing
        agents both downgrade to the legacy Anthropic path, because a
        stalled spawn is worse than a routing miss.
        """
        role = get_agent_role(agent_slug) or ""

        # 1) agent override
        resolved = await self._find_assignment(
            scope=AssignmentScope.AGENT_SLUG, scope_value=agent_slug
        )
        # 2) role override
        if resolved is None and role:
            resolved = await self._find_assignment(
                scope=AssignmentScope.ROLE, scope_value=role
            )
        # 3) global default
        if resolved is None:
            resolved = await self._find_assignment(
                scope=AssignmentScope.GLOBAL, scope_value=None
            )

        if resolved is not None and resolved.provider.enabled:
            try:
                return await self._route_from_assignment(resolved)
            except EncryptionError:
                self.log.error(
                    "Provider token decrypt failed; falling back to legacy path",
                    provider_id=str(resolved.provider.id),
                    agent_slug=agent_slug,
                )

        # 4) legacy fallback: role-default short name through MODEL_MAP.
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
    ) -> ModelAssignmentTable:
        """Insert-or-update (by unique (scope, scope_value)).

        Provider is derived from `MODEL_CATALOG` — the UI never picks a
        provider separately, so the service looks up the pre-seeded
        provider row for the catalog entry's type.
        """
        self._validate_scope(scope, scope_value)
        entry = MODEL_CATALOG_BY_NAME.get(model_name)
        if entry is None:
            raise ValueError(
                f"Unknown model '{model_name}'. Use one from "
                "GET /api/providers/catalog."
            )
        provider = await self._get_seeded_provider(entry.provider_type)

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
            provider_type=entry.provider_type.value,
            model_name=model_name,
        )
        return row

    async def derive_mode(self) -> str:
        """Return the current "mode" label for the Settings UI.

        Decision tree matches what `apply_mode` writes:
          - no assignments at all      → "anthropic"
          - only a global row, Ollama  → "ollama"
          - anything else              → "mix"
        """
        assignments = await self.list_assignments()
        if not assignments:
            return "anthropic"
        only_global = (
            len(assignments) == 1 and assignments[0].scope == AssignmentScope.GLOBAL
        )
        is_ollama = (
            only_global and assignments[0].provider.type == ModelProvider.OLLAMA_CLOUD
        )
        if is_ollama:
            return "ollama"
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
          - "anthropic": wipe all assignments so every spawn falls through
            to the legacy ROLE_MODEL_MAP + mounted ~/.claude path.
          - "ollama":    wipe role/agent overrides, set GLOBAL to the given
            Ollama model (default: Kimi K2.6). CEO-type pins can be layered
            back manually if the user wants them.
          - "mix":       apply per-agent map verbatim. Any agent not in the
            map falls through to the GLOBAL default — which is whatever it
            was (preserves prior state).
        """
        if mode == "anthropic":
            await self.session.execute(sa_delete(ModelAssignmentTable))
            await self.session.flush()
            self.log.info("Mode applied: anthropic (all assignments cleared)")
            return

        if mode == "ollama":
            await self.session.execute(sa_delete(ModelAssignmentTable))
            await self.session.flush()
            await self.upsert_assignment(
                scope=AssignmentScope.GLOBAL,
                scope_value=None,
                model_name=default_model or OLLAMA_DEFAULT_MODEL,
            )
            self.log.info(
                "Mode applied: ollama",
                default_model=default_model or OLLAMA_DEFAULT_MODEL,
            )
            return

        if mode == "mix":
            if not per_agent:
                raise ValueError("mix mode requires a per_agent map")
            # Clear existing agent-slug overrides so the new map is
            # authoritative; leave role + global alone.
            await self.session.execute(
                sa_delete(ModelAssignmentTable).where(
                    ModelAssignmentTable.scope == AssignmentScope.AGENT_SLUG
                )
            )
            await self.session.flush()
            for agent_slug, model_name in per_agent.items():
                if not model_name:
                    continue
                await self.upsert_assignment(
                    scope=AssignmentScope.AGENT_SLUG,
                    scope_value=agent_slug,
                    model_name=model_name,
                )
            self.log.info("Mode applied: mix", agents=len(per_agent))
            return

        raise ValueError(f"Unknown mode '{mode}'. Use 'anthropic', 'ollama', or 'mix'.")

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
