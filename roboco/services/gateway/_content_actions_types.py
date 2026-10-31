"""Structural type for the composed ContentActions, TYPE_CHECKING-only.

``ContentActions`` is composed from mixins (``content_actions*.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into the core
class's own helpers and service properties, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of
``ContentActionsSelf``, which declares that cross-mixin surface: ``FooSurface``
holds what ``FooActions`` offers the other mixins (``ContentSurface`` what
``ContentActions`` itself does), as ``*args``/``**kwargs`` stubs with the real
return type, decorator and async-ness, so no moved method body changes.
``tests/unit/gateway/test_content_actions_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.db.tables import TaskTable
    from roboco.services.gateway.content_actions import ContentActionsDeps
    from roboco.services.gateway.envelope import Envelope


class BoardProposalSurface(Protocol):
    """``BoardProposalActions``'s members that other mixins reach."""

    async def _trace_board_proposal(self, *args: Any, **kwargs: Any) -> None: ...


class ContentSurface(Protocol):
    """``ContentActions``'s members that other mixins reach."""

    async def _active_claim_violation(
        self, *args: Any, **kwargs: Any
    ) -> Envelope | None: ...
    async def _caller_is_claimless_role(self, *args: Any, **kwargs: Any) -> bool: ...
    async def _caller_role(self, *args: Any, **kwargs: Any) -> str: ...
    @staticmethod
    def _reject_soup(*args: Any, **kwargs: Any) -> Envelope | None: ...
    @classmethod
    def _reject_structured_soup(cls, *args: Any, **kwargs: Any) -> Envelope | None: ...
    async def _touch_heartbeat(self, *args: Any, **kwargs: Any) -> None: ...
    @property
    def a2a(self) -> Any: ...
    @property
    def evidence_repo(self) -> Any: ...
    @property
    def git(self) -> Any: ...
    @property
    def journal(self) -> Any: ...
    @property
    def notifications(self) -> Any: ...
    @property
    def orchestrator(self) -> Any: ...
    @property
    def task(self) -> Any: ...
    @property
    def workspace(self) -> Any: ...


class RoadmapSurface(Protocol):
    """``RoadmapActions``'s members that other mixins reach."""

    @staticmethod
    def _reject_roadmap_item_team(*args: Any, **kwargs: Any) -> Envelope | None: ...


class SandboxSurface(Protocol):
    """``SandboxActions``'s members that other mixins reach."""

    async def _sandbox_active_task(
        self, *args: Any, **kwargs: Any
    ) -> tuple[Any, Envelope | None]: ...


class SandboxTestsSurface(Protocol):
    """``SandboxTestsActions``'s members that other mixins reach."""

    @staticmethod
    async def _git_capture(*args: Any, **kwargs: Any) -> tuple[bool, str, str]: ...


class TaskAccessSurface(Protocol):
    """``TaskAccessActions``'s members that other mixins reach."""

    async def _evidence_task_or_error(
        self, *args: Any, **kwargs: Any
    ) -> TaskTable | Envelope: ...
    async def _resolve_task_ref(self, *args: Any, **kwargs: Any) -> UUID | Envelope: ...
    async def _verify_explicit_task_ownership(
        self, *args: Any, **kwargs: Any
    ) -> Envelope | None: ...


class ContentActionsSelf(
    BoardProposalSurface,
    ContentSurface,
    RoadmapSurface,
    SandboxSurface,
    SandboxTestsSurface,
    TaskAccessSurface,
    Protocol,
):
    """Every cross-mixin member a moved method body reaches via ``self``:
    the service deps here, each class's members on its surface."""

    _deps: ContentActionsDeps
