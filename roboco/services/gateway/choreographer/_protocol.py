"""Typed stub mixins inherit from for static analysis.

The role mixins (`board.py`, `doc.py`, `qa.py`, …) call methods like
``self.task.get(...)`` and ``self._emit_rejection(...)`` that live on
the legacy ``Choreographer`` class in ``_impl.py``. Without a typed
reference, mypy resolves those as ``Any`` (via ``# type: ignore[attr-defined]``)
which then bubbles up as ``no-any-return`` errors at every Envelope-returning
verb.

This module gives mypy a typed view of those helpers. ``ChoreographerHelpers``
is **not** a Protocol — Protocol with abstract members causes the
composed ``Choreographer`` class to be flagged as instantiating an
abstract class. Instead it's a plain class with stub signatures used
ONLY under ``TYPE_CHECKING``; at runtime mixins inherit from ``object``
so there's no abstract-method baggage. The real implementations live
on ``_LegacyChoreographer`` and are picked up by Python's MRO when the
composed ``Choreographer`` runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.services.gateway.choreographer._verb_runner import VerbRunner
    from roboco.services.gateway.envelope import Envelope


class ChoreographerHelpers:
    """Typed stub of attributes + helpers role mixins call on ``self``.

    Stub bodies (``...``) are never executed; the real methods live on
    ``_LegacyChoreographer`` and resolve via MRO at runtime.
    """

    task: Any
    work_session: Any
    git: Any
    a2a: Any
    journal: Any
    audit: Any
    evidence_repo: Any

    async def _emit_rejection(
        self,
        env: Envelope,
        *,
        agent_id: UUID,
        task_id: UUID | None,
        verb: str,
    ) -> Envelope:
        raise NotImplementedError

    async def _briefing_for(
        self,
        agent_id: UUID,
        task_id: UUID | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _with_briefing(
        env: Envelope,
        briefing: dict[str, Any],
    ) -> Envelope:
        raise NotImplementedError

    async def _run_claim_guards(
        self,
        *,
        agent_id: UUID,
        task: Any,
    ) -> Envelope | None:
        raise NotImplementedError

    async def _touch(self, task_id: UUID | None) -> None:
        raise NotImplementedError

    def _verb_runner(self) -> VerbRunner:
        raise NotImplementedError

    async def _build_tracing_gap(
        self,
        agent_id: UUID,
        task_id: UUID,
        missing: list[str],
    ) -> Envelope:
        raise NotImplementedError
