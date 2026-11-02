"""Structural type for the composed _GitReleaseOps, TYPE_CHECKING-only.

``_GitReleaseOps`` is composed from mixins (``roboco/services/release_executor.py``)
whose methods were moved verbatim out of the one class. mypy checks each mixin against
its own bases only, so a call into a sibling mixin, or into _GitReleaseOps's own
members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``_GitReleaseOpsSelf``, which
declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the other
classes (``_GitReleaseOpsSurface`` what ``_GitReleaseOps`` itself does), with each
method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/test_git_release_ops_mixins.py`` fails on a stub its surface's
class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.services.git_relay import GitRelay
    from roboco.services.release_executor import _PromotionStep


class _GitReleaseOpsSurface(Protocol):
    """``_GitReleaseOps``'s members that other classes reach."""

    async def _forge_token(self) -> str | None: ...
    async def _git(self, *args: str) -> tuple[int, str]: ...
    async def _rev_parse(self, ref: str) -> str: ...


class _GitReleaseOpsSelf(_GitReleaseOpsSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    _GitReleaseOps's instance attributes here, each class's members on its surface."""

    _ci_workflow: str | None
    _default_branch: str
    _env_chain: list[str]
    _git_url: str
    _head_branch: str
    _promotion: list[_PromotionStep]
    _relay: GitRelay
    _root: Path
    _session: AsyncSession
    _slug: str
