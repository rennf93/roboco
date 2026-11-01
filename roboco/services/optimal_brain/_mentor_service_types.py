"""Structural type for the composed MentorService, TYPE_CHECKING-only.

``MentorService`` is composed from mixins (``roboco/services/optimal_brain/mentor.py``)
whose methods were moved verbatim out of the one class. mypy checks each mixin against
its own bases only, so an attribute a moved body reads through ``self`` would not
resolve.

``MentorServiceSelf``, each mixin's TYPE_CHECKING-only base, declares those attributes.
No moved body calls into a sibling mixin or into MentorService's own methods, so there
is no ``FooSurface`` stub yet. One added later goes here, and
``tests/unit/services/optimal_brain/test_mentor_service_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from roboco.models.optimal import MentorConversation


class MentorServiceSelf(Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    MentorService's attributes."""

    _memory_store: dict[str, MentorConversation]
    _optimal_service: Any
    _redis: Any | None
