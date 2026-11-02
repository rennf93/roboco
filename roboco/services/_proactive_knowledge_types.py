"""Structural type for the composed ProactiveKnowledgeService, TYPE_CHECKING-only.

``ProactiveKnowledgeService`` is composed from mixins (``roboco/services/proactive.py``)
whose methods were moved verbatim out of the one class. mypy checks each mixin against
its own bases only, so an attribute a moved body reads through ``self`` would not
resolve.

``ProactiveKnowledgeServiceSelf``, each mixin's TYPE_CHECKING-only base, declares those
attributes. No moved body calls into a sibling mixin or into ProactiveKnowledgeService's
own methods, so there is no ``FooSurface`` stub yet. One added later goes here, and
``tests/unit/services/test_proactive_knowledge_service_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import Any, Protocol


class ProactiveKnowledgeServiceSelf(Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    ProactiveKnowledgeService's attributes."""

    _optimal_service: Any
