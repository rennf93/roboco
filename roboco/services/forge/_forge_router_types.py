"""Structural type for the composed ForgeRouter, TYPE_CHECKING-only.

``ForgeRouter`` is composed from mixins (``roboco/services/forge/router.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin against its own
bases only, so a call into a sibling mixin, or into ForgeRouter's own members, would not
resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``ForgeRouterSelf``, which
declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the other
classes (``ForgeRouterSurface`` what ``ForgeRouter`` itself does), with each method's
own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/forge/test_forge_router_mixins.py`` fails on a stub its surface's
class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from roboco.services.forge.base import GitProvider, RepoRef


class ForgeRouterSurface(Protocol):
    """``ForgeRouter``'s members that other classes reach."""

    @staticmethod
    def _provider_for_ref(ref: RepoRef) -> GitProvider: ...


class ForgeRouterSelf(ForgeRouterSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``: each class's
    members on its surface."""
