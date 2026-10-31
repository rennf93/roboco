"""Structural type for the composed XEngine, TYPE_CHECKING-only.

``XEngine`` is composed from mixins (``roboco/services/x_engine.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
XEngine's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``XEngineSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``XSurface`` what ``XEngine`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_x_engine_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.x_client import XClient


class XSurface(Protocol):
    """``XEngine``'s members that other classes reach."""

    async def _client(self) -> XClient: ...
    async def _project_or_default(
        self, project_id: UUID | None
    ) -> ProjectTable | None: ...
    async def _roboco_project(self) -> ProjectTable | None: ...
    async def _voice_guide(self, product_name: str) -> str: ...


class XPostMaterializationSurface(Protocol):
    """``XPostMaterialization``'s members that other classes reach."""

    async def _originate_post(
        self, *, title: str, body: str, source: str, project_id: UUID
    ) -> TaskTable: ...


class XEngineSelf(XSurface, XPostMaterializationSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and XEngine's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
    _injected_client: Any
