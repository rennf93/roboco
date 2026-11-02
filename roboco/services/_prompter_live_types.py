"""Structural type for the composed PrompterLiveRegistry, TYPE_CHECKING-only.

``PrompterLiveRegistry`` is composed from mixins (``roboco/services/prompter_live.py``)
whose methods were moved verbatim out of the one class. mypy checks each mixin against
its own bases only, so an attribute a moved body reads through ``self`` would not
resolve.

``PrompterLiveRegistrySelf``, each mixin's TYPE_CHECKING-only base, declares those
attributes. No moved body calls into a sibling mixin or into PrompterLiveRegistry's own
methods, so there is no ``FooSurface`` stub yet. One added later goes here, and
``tests/unit/services/test_prompter_live_registry_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import httpx

    from roboco.services.prompter_live import LiveIntakeSession


class PrompterLiveRegistrySelf(Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    PrompterLiveRegistry's attributes."""

    _client: httpx.AsyncClient | None
    _sessions: dict[str, LiveIntakeSession]
    log: Any
