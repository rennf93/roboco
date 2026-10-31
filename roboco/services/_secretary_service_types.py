"""Structural type for the composed SecretaryService, TYPE_CHECKING-only.

``SecretaryService`` is composed from mixins (``roboco/services/secretary.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
SecretaryService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``SecretaryServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``SecretarySurface`` what ``SecretaryService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_secretary_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SecretaryServiceSelf(Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and SecretaryService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
