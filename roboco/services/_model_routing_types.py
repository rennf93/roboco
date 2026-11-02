"""Structural type for the composed ModelRoutingService, TYPE_CHECKING-only.

``ModelRoutingService`` is composed from mixins (``roboco/services/llm.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin against its own
bases only, so a call into a sibling mixin, or into ModelRoutingService's own members,
would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``ModelRoutingServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the
other classes (``ModelRoutingSurface`` what ``ModelRoutingService`` itself does), with
each method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/test_model_routing_service_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from typing import Literal

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ModelAssignmentTable, ProviderConfigTable
    from roboco.models.base import AssignmentScope, ModelProvider
    from roboco.services.llm import AgentRoute, _ResolvedAssignment


class ModelAssignmentsSurface(Protocol):
    """``ModelAssignments``'s members that other classes reach."""

    async def derive_mode(
        self,
    ) -> Literal[
        "anthropic",
        "grok",
        "codex",
        "gemini",
        "kimi",
        "openrouter",
        "nebius",
        "zai",
        "hummin",
        "ollama",
        "mix",
        "self_hosted",
    ]: ...
    async def list_assignments(self) -> list[ModelAssignmentTable]: ...
    async def upsert_assignment(
        self,
        *,
        scope: AssignmentScope,
        scope_value: str | None,
        model_name: str,
        provider_type_override: ModelProvider | None = ...,
    ) -> ModelAssignmentTable: ...


class ModelModeSwitchSurface(Protocol):
    """``ModelModeSwitch``'s members that other classes reach."""

    async def _wipe_mode_switch_assignments(self) -> None: ...


class ModelProviderModesSurface(Protocol):
    """``ModelProviderModes``'s members that other classes reach."""

    async def _apply_codex(self, default_model: str | None) -> None: ...
    async def _apply_gemini(self, default_model: str | None) -> None: ...
    async def _apply_grok(self, default_model: str | None) -> None: ...
    async def _apply_hummin(self, default_model: str | None) -> None: ...
    async def _apply_kimi(self, default_model: str | None) -> None: ...
    async def _apply_nebius(self, default_model: str | None) -> None: ...
    async def _apply_openrouter(self, default_model: str | None) -> None: ...
    async def _apply_zai(self, default_model: str | None) -> None: ...


class ModelRouteResolutionSurface(Protocol):
    """``ModelRouteResolution``'s members that other classes reach."""

    async def _get_seeded_provider(
        self, provider_type: ModelProvider
    ) -> ProviderConfigTable: ...
    async def resolve_provider_for_model(
        self, model_name: str
    ) -> ProviderConfigTable | None: ...


class ModelRoutingSurface(Protocol):
    """``ModelRoutingService``'s members that other classes reach."""

    async def _find_local_provider(self) -> ProviderConfigTable | None: ...
    async def _route_from_assignment(
        self, resolved: _ResolvedAssignment
    ) -> AgentRoute: ...
    @staticmethod
    def _validate_scope(scope: AssignmentScope, scope_value: str | None) -> None: ...


class ModelRoutingServiceSelf(
    ModelAssignmentsSurface,
    ModelModeSwitchSurface,
    ModelProviderModesSurface,
    ModelRouteResolutionSurface,
    ModelRoutingSurface,
    Protocol,
):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and ModelRoutingService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
