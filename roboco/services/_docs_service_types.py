"""Structural type for the composed DocsService, TYPE_CHECKING-only.

``DocsService`` is composed from mixins (``roboco/services/docs.py``) whose methods were
moved verbatim out of the one class. mypy checks each mixin against its own bases only,
so an attribute a moved body reads through ``self`` would not resolve.

``DocsServiceSelf``, each mixin's TYPE_CHECKING-only base, declares those attributes. No
moved body calls into a sibling mixin or into DocsService's own methods, so there is no
``FooSurface`` stub yet. One added later goes here, and
``tests/unit/services/test_docs_service_mixins.py`` fails on a stub its surface's class
does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class DocsServiceSelf(Protocol):
    """Every cross-class member a moved method body reaches via ``self``: BaseService's
    and DocsService's attributes."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
