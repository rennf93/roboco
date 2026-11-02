"""Structural type for the composed VideoPostService, TYPE_CHECKING-only.

``VideoPostService`` is composed from mixins (``roboco/services/video_post_service.py``)
whose methods were moved verbatim out of the one class. mypy checks each mixin against
its own bases only, so an attribute a moved body reads through ``self`` would not
resolve.

``VideoPostServiceSelf``, each mixin's TYPE_CHECKING-only base, declares those
attributes. No moved body calls into a sibling mixin or into VideoPostService's own
methods, so there is no ``FooSurface`` stub yet. One added later goes here, and
``tests/unit/services/test_video_post_service_mixins.py`` fails on a stub its surface's
class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.services.video_post_service import TikTokPoster, XVideoPoster


class VideoPostServiceSelf(Protocol):
    """Every cross-class member a moved method body reaches via ``self``: BaseService's
    and VideoPostService's attributes."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
    _tiktok_poster: TikTokPoster
    _x_poster: XVideoPoster
