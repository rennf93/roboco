"""Standardized response envelope used by every gateway intent verb.

Every successful verb returns Envelope.ok(...). Every error returns one of
Envelope.tracing_gap / invalid_state / not_authorized / not_found. The
shape is the single contract that MCP servers convert into JSON for agents.

``correlation_id`` is intentionally NOT a constructor argument — it is a
transport-layer concern stamped post-construction by the route handler
from ``request.state.correlation_id`` (see
``api.routes.v2._role_dep.envelope_to_response``). Verb logic must never
thread it through; doing so would mix request lifecycle into business
logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Envelope:
    """Canonical gateway response. Convert to JSON via `as_dict()`."""

    status: str | None = None
    task_id: str | None = None
    # `next` shadows the built-in, but the name is part of the agent-facing
    # wire format and must not be renamed.
    next: str | None = None
    evidence: dict[str, Any] | None = None
    context_briefing: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    message: str | None = None
    remediate: str | None = None
    missing: list[str] | None = None
    # Stamped post-construction by the route layer from
    # ``request.state.correlation_id`` (set by ``CorrelationIdMiddleware``).
    # Carried back to the agent so the same id flows MCP -> API -> agent
    # and ops can join logs across the full hop.
    correlation_id: str | None = None

    @classmethod
    def ok(
        cls,
        *,
        status: str,
        task_id: str | None = None,
        next: str,
        evidence: dict[str, Any] | None = None,
        context_briefing: dict[str, Any] | None = None,
    ) -> Envelope:
        return cls(
            status=status,
            task_id=task_id,
            next=next,
            evidence=evidence,
            context_briefing=context_briefing or {},
        )

    @classmethod
    def tracing_gap(
        cls,
        *,
        missing: list[str],
        remediate: str,
        context_briefing: dict[str, Any] | None = None,
    ) -> Envelope:
        return cls(
            error="tracing_gap",
            missing=missing,
            remediate=remediate,
            context_briefing=context_briefing or {},
        )

    @classmethod
    def invalid_state(
        cls,
        *,
        message: str,
        remediate: str,
        context_briefing: dict[str, Any] | None = None,
    ) -> Envelope:
        return cls(
            error="invalid_state",
            message=message,
            remediate=remediate,
            context_briefing=context_briefing or {},
        )

    @classmethod
    def not_authorized(
        cls,
        *,
        message: str,
        remediate: str,
        context_briefing: dict[str, Any] | None = None,
    ) -> Envelope:
        return cls(
            error="not_authorized",
            message=message,
            remediate=remediate,
            context_briefing=context_briefing or {},
        )

    @classmethod
    def not_found(cls, *, message: str) -> Envelope:
        return cls(error="not_found", message=message, context_briefing={})

    def as_dict(self) -> dict[str, Any]:
        """Wire-format dict. Drops None fields except `error` (always present)."""
        out: dict[str, Any] = {
            "status": self.status,
            "task_id": self.task_id,
            "next": self.next,
            "evidence": self.evidence or {},
            "context_briefing": self.context_briefing,
            "error": self.error,
            "correlation_id": self.correlation_id,
        }
        if self.error is not None:
            out["message"] = self.message
            out["remediate"] = self.remediate
            if self.missing is not None:
                out["missing"] = self.missing
        return out
