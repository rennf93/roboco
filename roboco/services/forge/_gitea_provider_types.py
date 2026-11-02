"""Structural type for the composed GiteaProvider, TYPE_CHECKING-only.

``GiteaProvider`` is composed from mixins (``roboco/services/forge/gitea.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin against its own
bases only, so a call into a sibling mixin, or into GiteaProvider's own members, would
not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``GiteaProviderSelf``, which
declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the other
classes (``GiteaProviderSurface`` what ``GiteaProvider`` itself does), with each
method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/forge/test_gitea_provider_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import re
    from collections.abc import Mapping

    import httpx

    from roboco.services.forge.base import RepoRef


class GiteaProviderSurface(Protocol):
    """``GiteaProvider``'s members that other classes reach."""

    def _api_base(self) -> str: ...
    @staticmethod
    def _headers(token: str) -> dict[str, str]: ...
    def _repo_url(self, repo: RepoRef, *segments: str) -> str: ...
    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: dict[str, Any] | None = ...,
        params: dict[str, Any] | None = ...,
        client: httpx.AsyncClient | None = ...,
        timeout: float | None = ...,
    ) -> httpx.Response: ...


class GiteaProviderSelf(GiteaProviderSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    GiteaProvider's instance attributes here, each class's members on its surface."""

    _host: str
    _repo_url_re: re.Pattern[str]
    _scheme: str
