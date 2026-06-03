"""Role-asserting dependencies and shared helpers for v1 flow routers.

Every router gets one of these as a dependency so the role check happens
before the choreographer body even runs. Defense in depth — the
choreographer also re-checks role internally for verbs that branch on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import Depends, Header, HTTPException, params, status

from roboco.foundation.identity import Role

if TYPE_CHECKING:
    from fastapi import Request

    from roboco.services.gateway.envelope import Envelope


def _require_roles(allowed: frozenset[Role]) -> params.Depends:
    def _check(
        x_agent_role: Annotated[str, Header(alias="X-Agent-Role")],
    ) -> None:
        # `Role` is a StrEnum, so the lowercase header string compares equal
        # to its matching member.
        if x_agent_role.lower() not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{x_agent_role}' not allowed for this endpoint group",
            )

    return cast("params.Depends", Depends(_check))


# Role-typed single-role guards — renaming a role edits foundation.identity only.
# `require_board` is the only multi-role guard (Product Owner + Head of Marketing
# share the public-facing board endpoints; the auditor has its own guard).
require_dev = _require_roles(frozenset({Role.DEVELOPER}))
require_qa = _require_roles(frozenset({Role.QA}))
require_doc = _require_roles(frozenset({Role.DOCUMENTER}))
require_cell_pm = _require_roles(frozenset({Role.CELL_PM}))
require_main_pm = _require_roles(frozenset({Role.MAIN_PM}))
require_board = _require_roles(frozenset({Role.PRODUCT_OWNER, Role.HEAD_MARKETING}))
require_auditor = _require_roles(frozenset({Role.AUDITOR}))


def envelope_to_response(env: Envelope, request: Request) -> dict[str, Any]:
    """Stamp the request's correlation_id onto the envelope and return wire-dict.

    ``CorrelationIdMiddleware`` writes the inbound (or freshly-generated)
    ``X-Correlation-ID`` to ``request.state.correlation_id``. We pull it
    here so the agent receives the same id it sent (or can capture the
    server-generated one) and ops can join logs across the full
    MCP -> API -> service hop.
    """
    cid = getattr(request.state, "correlation_id", None)
    if cid is not None and env.correlation_id is None:
        env.correlation_id = cid
    return env.as_dict()
