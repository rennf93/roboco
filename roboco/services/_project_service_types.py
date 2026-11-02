"""Structural type for the composed ProjectService, TYPE_CHECKING-only.

``ProjectService`` is composed from mixins (``roboco/services/project.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
ProjectService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``ProjectServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``ProjectSurface`` what ``ProjectService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_project_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable


class ProjectSurface(Protocol):
    """``ProjectService``'s members that other classes reach."""

    async def get(self, project_id: UUID) -> ProjectTable | None: ...
    async def get_by_slug(self, slug: str) -> ProjectTable | None: ...


class ProjectServiceSelf(ProjectSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and ProjectService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
