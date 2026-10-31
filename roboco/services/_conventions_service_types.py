"""Structural type for the composed ConventionsService, TYPE_CHECKING-only.

``ConventionsService`` is composed from mixins (``roboco/services/conventions.py``)
whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
ConventionsService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``ConventionsServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``ConventionsSurface`` what ``ConventionsService`` itself
does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_conventions_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ConventionsServiceSelf(Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and ConventionsService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
