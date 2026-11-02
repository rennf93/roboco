"""Structural type for the composed ReleaseProposalService, TYPE_CHECKING-only.

``ReleaseProposalService`` is composed from mixins
(``roboco/services/release_proposal.py``) whose methods were moved verbatim out of the
one class. mypy checks each mixin against its own bases only, so an attribute a moved
body reads through ``self`` would not resolve.

``ReleaseProposalServiceSelf``, each mixin's TYPE_CHECKING-only base, declares those
attributes. No moved body calls into a sibling mixin or into ReleaseProposalService's
own methods, so there is no ``FooSurface`` stub yet. One added later goes here, and
``tests/unit/services/test_release_proposal_service_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    import redis.asyncio as redis
    from sqlalchemy.ext.asyncio import AsyncSession


class ReleaseProposalServiceSelf(Protocol):
    """Every cross-class member a moved method body reaches via ``self``: BaseService's
    and ReleaseProposalService's attributes."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
    _redis: redis.Redis | None = None
