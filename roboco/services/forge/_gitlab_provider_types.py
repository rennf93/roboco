"""Structural type for the composed GitLabProvider, TYPE_CHECKING-only.

``GitLabProvider`` is composed from mixins (``roboco/services/forge/gitlab.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin against its own
bases only, so a call into a sibling mixin, or into GitLabProvider's own members, would
not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``GitLabProviderSelf``, which
declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the other
classes (``GitLabProviderSurface`` what ``GitLabProvider`` itself does), with each
method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/forge/test_gitlab_provider_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import re
    from collections.abc import Mapping

    import httpx

    from roboco.services.forge.base import RepoRef


class GitLabProviderSurface(Protocol):
    """``GitLabProvider``'s members that other classes reach."""

    @staticmethod
    def _headers(token: str) -> dict[str, str]: ...
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
    def _url(self, repo: RepoRef, *segments: str) -> str: ...


class GitLabProviderSelf(GitLabProviderSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    GitLabProvider's instance attributes here, each class's members on its surface."""

    _host: str
    _repo_url_re: re.Pattern[str]
    _scheme: str
