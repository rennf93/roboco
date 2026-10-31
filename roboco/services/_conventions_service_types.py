"""Structural type for the composed ConventionsService, TYPE_CHECKING-only.

``ConventionsService`` is composed from mixins (``roboco/services/conventions.py``)
whose methods were moved verbatim out of the one class. mypy checks each mixin against
its own bases only, so an attribute a moved body reads through ``self`` would not
resolve.

``ConventionsServiceSelf``, each mixin's TYPE_CHECKING-only base, declares those
attributes. No moved body calls into a sibling mixin or into ConventionsService's own
methods, so there is no ``FooSurface`` stub yet. One added later goes here, and
``tests/unit/services/test_conventions_service_mixins.py`` fails on a stub its surface's
class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ConventionsServiceSelf(Protocol):
    """Every cross-class member a moved method body reaches via ``self``: BaseService's
    and ConventionsService's attributes."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
