"""Standardized response envelope used by every gateway intent verb.

Every successful verb returns Envelope.ok(...). Every error returns one of
Envelope.tracing_gap / invalid_state / not_authorized / not_found. The
shape is the single contract that MCP servers convert into JSON for agents.

``correlation_id`` is intentionally NOT a constructor argument — it is a
transport-layer concern stamped post-construction by the route handler
from ``request.state.correlation_id`` (see
``api.routes.v1._role_dep.envelope_to_response``). Verb logic must never
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
    # Populated only by `incomplete_input` envelopes — the literal
    # answer-key the agent uses to re-issue the call (spec §5.2.1).
    field_hints: dict[str, str] | None = None
    # Stamped post-construction by the route layer from
    # ``request.state.correlation_id`` (set by ``CorrelationIdMiddleware``).
    # Carried back to the agent so the same id flows MCP -> API -> agent
    # and ops can join logs across the full hop.
    correlation_id: str | None = None
    # Introspection — populated by `with_introspection(task, role)` so
    # agents can see the task's current status and the verbs they can
    # usefully call next without trial-and-error against the gateway.
    # Both default to None when no task context is available (e.g. for
    # tool-discovery envelopes).
    current_state: str | None = None
    valid_next_verbs: list[str] | None = None

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

    @staticmethod
    def _missing_message(label: str, missing: list[str], remediate: str) -> str:
        """Human-readable rejection summary so `message` is never null.

        `tracing_gap`/`incomplete_input` historically left `message` unset, so
        the agent (and the audit log, which records `message`) saw `null` and
        could not see what to do — the agent then retried the same verb until it
        burned out. This folds the missing tokens + the actionable `remediate`
        into one line carried by `message`, so a client reading only `message`
        still gets the full picture.
        """
        joined = ", ".join(missing) if missing else "(unspecified)"
        base = f"blocked: {label} missing — {joined}"
        return f"{base}. {remediate}" if remediate else base

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
            message=cls._missing_message("required tracing", missing, remediate),
            missing=missing,
            remediate=remediate,
            context_briefing=context_briefing or {},
        )

    @classmethod
    def incomplete_input(
        cls,
        *,
        missing: list[str],
        field_hints: dict[str, str],
        remediate: str,
        context_briefing: dict[str, Any] | None = None,
    ) -> Envelope:
        """Structured rejection for under-filled inputs (spec §5.2.1).

        Distinct from `tracing_gap`. The agent receives a literal
        answer-key (`field_hints`) and re-issues the call with each
        missing field filled.
        """
        return cls(
            error="incomplete_input",
            message=cls._missing_message("required input fields", missing, remediate),
            missing=missing,
            field_hints=field_hints,
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

    @classmethod
    def circuit_open(
        cls,
        *,
        verb: str,
        attempts: int,
        window_seconds: int,
        remediate: str,
        context_briefing: dict[str, Any] | None = None,
    ) -> Envelope:
        """Per-verb retry circuit-breaker tripped — too many attempts in a window.

        Distinct from `tracing_gap` and `incomplete_input`. The agent receives
        a structured "stop hammering this verb" signal with a remediate hint
        pointing to i_am_blocked() / i_am_idle() as graceful exits. Wired by
        the agent_sdk runtime tracker (Phase 3 Task 14) — the gateway itself
        does not raise this.
        """
        return cls(
            error="circuit_open",
            message=(
                f"verb {verb!r} rejected {attempts} times in last "
                f"{window_seconds}s — circuit breaker open"
            ),
            remediate=remediate,
            context_briefing=context_briefing or {},
        )

    @classmethod
    def from_decision(
        cls, decision: Any, *, briefing: dict[str, Any] | None = None
    ) -> Envelope:
        """Map a lifecycle.spec.Decision rejection onto the right envelope flavor.

        Allow Decisions are a programming error here — call sites must
        check `decision.allowed` before invoking this.
        """
        if decision.allowed:
            raise ValueError("cannot build rejection from allow Decision")
        ctx = briefing or {}
        kind = decision.rejection_kind
        if kind == "tracing_gap":
            missing = list(decision.missing)
            remediate = decision.remediate or ""
            return cls(
                error="tracing_gap",
                message=cls._missing_message("required tracing", missing, remediate),
                missing=missing,
                remediate=remediate,
                context_briefing=ctx,
            )
        if kind == "self_review":
            return cls(
                error="not_authorized",
                message=(decision.message or "") + " (self-review blocked)",
                remediate=decision.remediate,
                context_briefing=ctx,
            )
        if kind == "not_found":
            return cls(
                error="not_found",
                message=decision.message,
                context_briefing=ctx,
            )
        # not_authorized | invalid_state — direct map
        return cls(
            error=kind,
            message=decision.message,
            remediate=decision.remediate,
            context_briefing=ctx,
        )

    def with_introspection(self, *, task: Any, role: str) -> Envelope:
        """Populate `current_state` and `valid_next_verbs` from a task + role.

        Returns self for chaining. Imports the lifecycle spec lazily so
        envelope.py stays importable from any layer without dragging in
        the canonical spec module. Unknown roles or malformed task
        statuses yield `[]` — preserves the legacy verb_gates contract
        of "introspection is best-effort and never raises".
        """
        from roboco.foundation.policy import lifecycle as spec

        try:
            self.current_state = str(getattr(task, "status", "") or "") or None
            role_enum = spec.Role(role)
            self.valid_next_verbs = spec.valid_next_verbs(role_enum, task)
        except Exception:
            # Introspection is best-effort and NEVER raises. Failure modes:
            # an unknown role string (ValueError); a mock/partial task
            # (TypeError); OR — critically, on an error path after a
            # rolled-back async session — reading task.status hits an EXPIRED
            # ORM attribute whose lazy reload fires outside the greenlet and
            # raises sqlalchemy MissingGreenlet. Any of these must degrade to
            # empty introspection rather than mask the real error this
            # envelope is reporting. (current_state defaults to None.)
            self.valid_next_verbs = []
        return self

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
            "current_state": self.current_state,
            "valid_next_verbs": self.valid_next_verbs,
        }
        if self.error is not None:
            out["message"] = self.message
            out["remediate"] = self.remediate
            if self.missing is not None:
                out["missing"] = self.missing
            if self.field_hints is not None:
                out["field_hints"] = self.field_hints
        return out
