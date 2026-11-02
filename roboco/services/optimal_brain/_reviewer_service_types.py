"""Structural type for the composed ReviewerService, TYPE_CHECKING-only.

``ReviewerService`` is composed from mixins
(``roboco/services/optimal_brain/reviewer.py``) whose methods were moved verbatim out of
the one class. mypy checks each mixin against its own bases only, so an attribute a
moved body reads through ``self`` would not resolve.

``ReviewerServiceSelf``, each mixin's TYPE_CHECKING-only base, declares those
attributes. No moved body calls into a sibling mixin or into ReviewerService's own
methods, so there is no ``FooSurface`` stub yet. One added later goes here, and
``tests/unit/services/optimal_brain/test_reviewer_service_mixins.py`` fails on a stub
its surface's class does not define.
"""

from __future__ import annotations

from typing import Any, Protocol


class ReviewerServiceSelf(Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    ReviewerService's attributes."""

    _optimal_service: Any
