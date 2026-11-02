"""Structural type for the composed PrompterService, TYPE_CHECKING-only.

``PrompterService`` is composed from mixins (``roboco/services/prompter.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
PrompterService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``PrompterServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``PrompterSurface`` what ``PrompterService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_prompter_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable
    from roboco.foundation.policy.sequencing.models import SequencePlan
    from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
    from roboco.services.prompter import BatchPlacement


class PrompterBatchConfirmSurface(Protocol):
    """``PrompterBatchConfirm``'s members that other classes reach."""

    def _sequence_drafts(self, drafts: list[dict[str, Any]]) -> SequencePlan: ...
    @staticmethod
    def _validate_batch_scope(
        drafts: list[dict[str, Any]], project_ids: list[UUID]
    ) -> None: ...


class PrompterDraftConfirmSurface(Protocol):
    """``PrompterDraftConfirm``'s members that other classes reach."""

    async def create_task_from_draft(
        self,
        draft_data: dict[str, Any],
        agent_id: UUID,
        *,
        status: TaskStatus = ...,
        assigned_to: UUID | None = ...,
        placement: BatchPlacement | None = ...,
    ) -> TaskTable: ...


class PrompterDraftValidationSurface(Protocol):
    """``PrompterDraftValidation``'s members that other classes reach."""

    @staticmethod
    def _coerce_draft_enums(
        draft_data: dict[str, Any],
    ) -> tuple[Team, TaskType, TaskNature, Complexity]: ...
    def _coerce_pm_code_to_planning(
        self,
        *,
        team: Team,
        task_type: TaskType,
        resolved_assigned_to: UUID | None,
        title: str,
    ) -> TaskType: ...
    @staticmethod
    def _coerce_priority(value: Any) -> int: ...
    @staticmethod
    def _lead_cell_team(draft_data: dict[str, Any], default: Team) -> Team: ...
    def _resolve_draft_assignee(
        self, assigned_to: UUID | None, draft_data: dict[str, Any]
    ) -> UUID | None: ...
    async def _resolve_owning_team(
        self,
        draft_data: dict[str, Any],
        *,
        resolved_product_id: UUID | None,
        resolved_assigned_to: UUID | None,
        team_override: Team | None,
        default_lead: Team,
    ) -> Team: ...
    def _validate_and_coerce_draft(self, draft_data: dict[str, Any]) -> None: ...
    @staticmethod
    def _validate_draft_target(
        project_id: UUID | None,
        product_id: UUID | None,
        *,
        is_umbrella: bool,
        has_cell_projects: bool = ...,
    ) -> None: ...


class PrompterSurface(Protocol):
    """``PrompterService``'s members that other classes reach."""

    @property
    def _session(self) -> AsyncSession: ...


class PrompterServiceSelf(
    PrompterBatchConfirmSurface,
    PrompterDraftConfirmSurface,
    PrompterDraftValidationSurface,
    PrompterSurface,
    Protocol,
):
    """Every cross-class member a moved method body reaches via ``self``:
    PrompterService's instance attributes here, each class's
    members on its surface."""

    _db: AsyncSession | None
    log: Any
