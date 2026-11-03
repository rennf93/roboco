"""Structural type for the composed LearningPropagationService, TYPE_CHECKING-only.

``LearningPropagationService`` is composed from mixins (``roboco/services/learning.py``)
whose methods were moved verbatim out of the one class. mypy checks each mixin against
its own bases only, so an attribute a moved body reads through ``self`` would not
resolve.

``LearningPropagationServiceSelf``, each mixin's TYPE_CHECKING-only base, declares those
attributes. No moved body calls into a sibling mixin or into
LearningPropagationService's own methods, so there is no ``FooSurface`` stub yet. One
added later goes here, and
``tests/unit/services/test_learning_propagation_service_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from roboco.services.learning import LearningNotification


class LearningPropagationServiceSelf(Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    LearningPropagationService's attributes."""

    _notification_queue: list[LearningNotification]
    _optimal_service: Any
